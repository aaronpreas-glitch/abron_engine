import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { Signal } from '../../types'
import { PctChange } from '../shared/PctChange'
import { Badge } from '../shared/Badge'
import { SignalModal } from './SignalModal'

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
  if (v < 0.01)     return `$${v.toFixed(8)}`.replace(/\.?0+$/, '')
  if (v < 1)        return `$${v.toFixed(6)}`.replace(/\.?0+$/, '')
  return `$${v.toFixed(4)}`.replace(/\.?0+$/, '')
}

function timeAgo(ts: string) {
  const diff = Date.now() - new Date(ts + 'Z').getTime()
  const s = Math.floor(diff / 1000)
  if (s < 60)    return `${s}s`
  if (s < 3600)  return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h`
  return `${Math.floor(s / 86400)}d`
}

function decisionColor(d: string): 'green' | 'amber' | 'red' | 'blue' | 'muted' {
  if (d.includes('ALERT') && !d.includes('DRY'))   return 'green'
  if (d.includes('DRY_RUN'))                        return 'blue'
  if (d.includes('WATCHLIST'))                      return 'amber'
  if (d.includes('RUNNER') || d.includes('LEGACY')) return 'green'
  if (d.includes('REGIME_BLOCK'))                   return 'red'
  return 'muted'
}

function decisionLabel(d: string): string {
  return d
    .replace('ALERT_DRY_RUN', 'DRY RUN')
    .replace('SCAN_BEST', 'SCAN')
    .replace('WATCHLIST_ALERT', 'WATCH')
    .replace('RUNNER_WATCH_ALERT', 'RUNNER')
    .replace('LEGACY_RECOVERY_ALERT', 'LEGACY')
    .replace(/_/g, ' ')
}

function convictionLabel(c: number | null): string | null {
  if (c === 3) return 'A'
  if (c === 2) return 'B'
  if (c === 1) return 'C'
  return null
}

function convictionColor(c: number | null): string {
  if (c === 3) return 'var(--green)'
  if (c === 2) return 'var(--amber)'
  return 'var(--muted)'
}

function scoreColor(score: number): string {
  if (score >= 85) return 'var(--green)'
  if (score >= 70) return 'var(--amber)'
  return 'var(--muted)'
}

function scoreTier(score: number): string {
  if (score >= 85) return 'ELITE'
  if (score >= 75) return 'HIGH'
  if (score >= 65) return 'MED'
  return 'LOW'
}

function dexUrl(mint: string | null, pairAddress: string | null): string | null {
  if (pairAddress) return `https://dexscreener.com/solana/${pairAddress}`
  if (mint) return `https://dexscreener.com/solana/${mint}`
  return null
}

function pumpUrl(mint: string | null): string | null {
  if (!mint) return null
  return `https://pump.fun/coin/${mint}`
}

function heliusColor(grade: string | null): string {
  if (grade === 'SAFE') return '#22c55e'
  if (grade === 'CAUTION') return '#f59e0b'
  if (grade === 'RISKY') return '#ef4444'
  if (grade === 'DANGER') return '#dc2626'
  return '#6b7280'
}
function heliusEmoji(grade: string | null): string {
  if (grade === 'SAFE') return '🛡'
  if (grade === 'CAUTION') return '⚠️'
  if (grade === 'RISKY') return '🚨'
  if (grade === 'DANGER') return '☠️'
  return ''
}

export function SignalCard({ sig }: { sig: Signal }) {
  const [modalOpen, setModalOpen] = useState(false)
  const navigate = useNavigate()

  const score   = sig.score_total
  const filled  = score != null ? Math.min(Math.round(score / 10), 10) : 0
  const bar     = '█'.repeat(filled) + '░'.repeat(10 - filled)
  const isAlert = sig.decision.includes('ALERT') && !sig.decision.includes('DRY')
  const grade   = convictionLabel(sig.conviction)
  const dex     = dexUrl(sig.mint, sig.pair_address)
  const pump    = pumpUrl(sig.mint)

  return (
    <>
      <div
        className="card"
        onClick={() => setModalOpen(true)}
        style={{
          marginBottom: 6,
          borderLeft: `3px solid ${isAlert ? 'var(--green)' : 'var(--border)'}`,
          padding: '10px 12px',
          cursor: 'pointer',
          transition: 'background 0.15s',
        }}
        onMouseEnter={e => (e.currentTarget.style.background = 'var(--surface2)')}
        onMouseLeave={e => (e.currentTarget.style.background = '')}
      >
        {/* Row 1: symbol + badges + links + time */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 6, flexWrap: 'wrap' }}>
          <span
            style={{ color: 'var(--text)', fontWeight: 700, fontSize: 14, cursor: 'pointer' }}
            onClick={e => { e.stopPropagation(); navigate(`/symbol/${sig.symbol}`) }}
            title={`View $${sig.symbol} history`}
          >
            ${sig.symbol}
          </span>

          <Badge label={decisionLabel(sig.decision)} color={decisionColor(sig.decision)} />

          {grade && (
            <span style={{
              padding: '1px 7px', borderRadius: 3, fontSize: 10, fontWeight: 700,
              letterSpacing: '0.06em',
              background: grade === 'A' ? '#39d35322' : grade === 'B' ? '#f0a50022' : '#ffffff11',
              color: convictionColor(sig.conviction),
              border: `1px solid ${convictionColor(sig.conviction)}44`,
            }}>
              {grade}
            </span>
          )}

          {sig.helius_grade && sig.helius_grade !== 'UNKNOWN' && (
            <span title={`On-chain safety: ${sig.helius_grade}`} style={{
              padding: '1px 6px', borderRadius: 3, fontSize: 10, fontWeight: 700,
              letterSpacing: '0.05em',
              background: heliusColor(sig.helius_grade) + '22',
              color: heliusColor(sig.helius_grade),
              border: `1px solid ${heliusColor(sig.helius_grade)}44`,
              cursor: 'default',
            }}>
              {heliusEmoji(sig.helius_grade)} {sig.helius_grade}
            </span>
          )}

          {sig.setup_type && sig.setup_type !== 'standard' && (
            <span style={{ color: 'var(--muted)', fontSize: 10, fontStyle: 'italic' }}>{sig.setup_type}</span>
          )}

          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10 }}>
            {/* Symbol history link */}
            <span
              style={{ color: 'var(--green)', fontSize: 10, cursor: 'pointer', opacity: 0.7, letterSpacing: '0.03em' }}
              onClick={e => { e.stopPropagation(); navigate(`/symbol/${sig.symbol}`) }}
              title="View full symbol history"
            >
              history →
            </span>
            {dex && (
              <a href={dex} target="_blank" rel="noopener noreferrer"
                style={{ color: '#58a6ff', fontSize: 10, textDecoration: 'none', letterSpacing: '0.03em' }}
                onClick={e => e.stopPropagation()}
              >
                DEX ↗
              </a>
            )}
            {pump && (
              <a href={pump} target="_blank" rel="noopener noreferrer"
                style={{ color: 'var(--muted)', fontSize: 10, textDecoration: 'none' }}
                onClick={e => e.stopPropagation()}
              >
                PUMP ↗
              </a>
            )}
            <span style={{ color: 'var(--muted)', fontSize: 11 }}>{timeAgo(sig.ts_utc)} ago</span>
          </div>
        </div>

        {/* Row 2: score bar + tier label + regime + rel strength */}
        {score != null && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 7 }}>
            <span style={{ fontFamily: 'monospace', fontSize: 11, color: scoreColor(score) }}>{bar}</span>
            <span style={{ fontSize: 12, fontWeight: 700, color: scoreColor(score) }}>{score.toFixed(0)}</span>
            <span style={{
              padding: '1px 6px', borderRadius: 2, fontSize: 9, fontWeight: 700,
              background: score >= 85 ? '#39d35322' : score >= 75 ? '#f0a50022' : '#ffffff0a',
              color: scoreColor(score),
            }}>
              {scoreTier(score)}
            </span>
            {sig.regime_score != null && (
              <span style={{ fontSize: 10, color: 'var(--muted)' }}>
                rgm <span style={{
                  fontWeight: 700,
                  color: sig.regime_score >= 50 ? 'var(--green)' : sig.regime_score >= 25 ? 'var(--amber)' : 'var(--red)',
                }}>
                  {sig.regime_score.toFixed(0)}
                </span>
              </span>
            )}
            {sig.rel_strength_vs_sol != null && (
              <span style={{ fontSize: 10, color: 'var(--muted)' }}>
                vs SOL <span style={{
                  fontWeight: 700,
                  color: sig.rel_strength_vs_sol >= 0 ? 'var(--green)' : 'var(--red)',
                }}>
                  {sig.rel_strength_vs_sol >= 0 ? '+' : ''}{sig.rel_strength_vs_sol.toFixed(1)}%
                </span>
              </span>
            )}
          </div>
        )}

        {/* Row 3: data grid (5 cols) */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: '3px 8px', fontSize: 11 }}>
          <div><span style={{ color: 'var(--muted)' }}>Price </span><span>{fmtPrice(sig.price_usd)}</span></div>
          <div><span style={{ color: 'var(--muted)' }}>Liq </span><span>{fmtUsd(sig.liquidity_usd)}</span></div>
          <div><span style={{ color: 'var(--muted)' }}>Vol24h </span><span>{fmtUsd(sig.volume_24h)}</span></div>
          <div><span style={{ color: 'var(--muted)' }}>24h </span><PctChange value={sig.change_24h} /></div>
          <div>
            <span style={{ color: 'var(--muted)' }}>Regime </span>
            <span style={{ fontSize: 10, color: 'var(--muted)' }}>
              {sig.regime_label?.replace(/_/g, ' ') || '—'}
            </span>
          </div>
        </div>

        {/* Notes */}
        {sig.notes && (
          <div style={{ marginTop: 5, color: 'var(--muted)', fontSize: 10, lineHeight: 1.4 }}>
            {sig.notes.slice(0, 160)}
          </div>
        )}

        {/* Click hint */}
        <div style={{ marginTop: 6, fontSize: 9, color: 'var(--border)', textAlign: 'right' }}>
          click card for breakdown · click $symbol for history
        </div>
      </div>

      {modalOpen && (
        <SignalModal signalId={sig.id} onClose={() => setModalOpen(false)} />
      )}
    </>
  )
}
