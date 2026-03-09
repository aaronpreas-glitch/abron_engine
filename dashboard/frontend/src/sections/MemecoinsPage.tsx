import { useState, useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api'

// ── Types ──────────────────────────────────────────────────────────────────

type RugLabel = 'GOOD' | 'WARN' | 'DANGER' | 'RUGGED' | 'UNKNOWN'

interface MemecoinSignal {
  mint:             string
  symbol:           string
  price:            number
  change_1h:        number | null
  change_24h:       number | null
  volume_24h:       number
  liquidity_usd:    number
  mcap_usd:         number
  token_age_days:   number
  vol_acceleration: number
  buy_pressure?:    number   // % of 1h txns that are buys (0-100); >60% bullish
  score:            number
  rug_label:        RugLabel
  top_holder_pct:   number
  lp_locked_pct:    number
  mint_revoked:     boolean
  freeze_revoked:   boolean
  dex_url:           string
  scanned_at:        string
  narrative?:        boolean
  narrative_sources?: string[]
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

interface ClosedTrade {
  symbol:      string
  mint:        string
  pnl_pct:     number
  pnl_usd:     number
  exit_reason: string
  closed_at:   string
}

interface LearnedThresholds {
  thresholds:  Record<string, number | boolean>
  sample_size: number
  win_rate:    number
  rug_stats:   Record<string, { count: number; win_rate: number }>
  updated_at:  string
  confidence:  'low' | 'medium' | 'high'
}

interface MemecoinsStatus {
  signals:            MemecoinSignal[]
  positions:          MemecoinPosition[]
  stats: {
    win_rate:     number
    total_pnl:    number
    closed_count: number
  }
  recent_closed:      ClosedTrade[]
  learned_thresholds: LearnedThresholds | null
}

// Patch 182: score threshold decision support
interface ScoreAnalysis {
  config_score_min: number
  verdict:    { label: string; message: string }
  threshold_sim: Array<{
    threshold: number; n: number; wr: number; avg_24h: number
    avg_win: number | null; avg_loss: number | null; is_current: boolean
  }>
  optimal_window: { lo: number; hi: number; n: number; wr: number; avg_24h: number } | null
  tuner: {
    min_score: number; max_score: number; confidence: string; sample_size: number; win_rate: number
    score_bands?: Array<{ lo: number; hi: number; n: number; wr: number; avg_24h: number; expectancy: number }>
    multi_band_mode?: boolean
    optimization_horizon?: string
  } | null
  bought_split: {
    bought:     { n: number; avg_score: number; wr: number; avg_24h: number }
    not_bought: { n: number; avg_score: number; wr: number; avg_24h: number }
  }
  bands: Array<{ lo: number; n: number; wr: number; avg_24h: number; avg_win: number | null; avg_loss: number | null }>
  horizon_comparison?: {
    n_both: number
    bands_4h:           Array<{ lo: number; hi: number; n: number; wr: number; avg_ret: number; expectancy: number }>
    bands_24h:          Array<{ lo: number; hi: number; n: number; wr: number; avg_ret: number; expectancy: number }>
    bands_missed_by_4h: Array<{ lo: number; hi: number; n: number; wr: number; avg_ret: number; expectancy: number }>
    active_horizon?: string
    verdict: { label: 'SWITCH_RECOMMENDED' | 'ALIGNED' | 'INSUFFICIENT_DATA' | 'ALREADY_SWITCHED' | 'CURRENTLY_OPTIMAL'; message: string }
    error?: string
  }
}

interface ScoreBucket {
  label:          string
  count:          number
  win_rate_4h:    number | null
  avg_return_1h:  number | null
  avg_return_4h:  number | null
  avg_return_24h: number | null
  buy_rate:       number | null
}

interface RugBucket {
  label:         string
  count:         number
  win_rate_4h:   number | null
  avg_return_4h: number | null
}

interface NarrativeCoin {
  symbol:  string
  name?:   string
  rank?:   number | null
  mint?:   string
  boosts?: number
  source:  string
}

interface NarrativeData {
  updated_at?:  string | null
  coingecko?:   NarrativeCoin[]
  dexscreener?: NarrativeCoin[]
}

interface AnalyticsData {
  total_tracked:      number
  complete:           number
  pending:            number
  bought_count:       number
  score_buckets:      ScoreBucket[]
  rug_breakdown:      RugBucket[]
  top_performers:     Array<{
    symbol: string; score: number; rug_label: string
    mcap_at_scan: number | null; token_age_days: number | null
    vol_acceleration: number | null; top_holder_pct: number | null
    return_1h_pct: number | null; return_4h_pct: number | null
    return_24h_pct: number | null; bought: number; scanned_at: string
  }>
  learned_thresholds: LearnedThresholds | null
  auto_buy?: {
    enabled:          boolean
    dry_run:          boolean
    score_min:        number
    max_open:         number
    buy_usd:          number
    tuner_threshold:  number
    complete_pct:     number
  }
  phase?:         number | null
  phase_label?:   string | null
  phase_desc?:    string | null
  next_milestone?: number | null
  phase_pct?:     number | null
}

// ── Helpers ────────────────────────────────────────────────────────────────

const MONO: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }

function fmtPrice(p: number): string {
  if (!p) return '—'
  if (p >= 1)      return `$${p.toFixed(4)}`
  if (p >= 0.0001) return `$${p.toFixed(6)}`
  return `$${p.toExponential(3)}`
}

function fmtMcap(v: number): string {
  if (!v || v <= 0) return '—'
  if (v >= 1_000_000_000) return `$${(v / 1_000_000_000).toFixed(1)}B`
  if (v >= 1_000_000)     return `$${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000)         return `$${(v / 1_000).toFixed(0)}k`
  return `$${v.toFixed(0)}`
}

function fmtAge(d: number): string {
  if (!d || d <= 0)   return '—'
  if (d < 1)          return `${Math.round(d * 24)}h`
  if (d < 100)        return `${d.toFixed(0)}d`
  return `${Math.round(d / 30)}mo`
}

function fmtRelTime(ts: string): string {
  if (!ts) return '—'
  try {
    const d   = new Date(ts.includes('T') ? ts : ts + 'Z')
    const ago = Math.floor((Date.now() - d.getTime()) / 1000)
    if (ago < 60)    return `${ago}s ago`
    if (ago < 3600)  return `${Math.floor(ago / 60)}m ago`
    if (ago < 86400) return `${Math.floor(ago / 3600)}h ago`
    return `${Math.floor(ago / 86400)}d ago`
  } catch { return '—' }
}

function scoreColor(s: number) {
  if (s >= 70) return '#00d48a'
  if (s >= 50) return '#f59e0b'
  return '#ef4444'
}

function pnlColor(n: number) { return n >= 0 ? '#00d48a' : '#ef4444' }

function rugColor(l: RugLabel | string) {
  if (l === 'GOOD')              return '#00d48a'
  if (l === 'WARN')              return '#f59e0b'
  if (l === 'DANGER' || l === 'RUGGED') return '#ef4444'
  return '#4d5a6e'
}

function rugEmoji(l: RugLabel | string) {
  if (l === 'GOOD')              return '🟢'
  if (l === 'WARN')              return '🟡'
  if (l === 'DANGER' || l === 'RUGGED') return '🔴'
  return '⚪'
}

function confidenceColor(c: string) {
  if (c === 'high')   return '#00d48a'
  if (c === 'medium') return '#f59e0b'
  return '#4d5a6e'
}

// ── Sub-components ─────────────────────────────────────────────────────────

function AgentBadge({ name, health }: { name: string; health?: string }) {
  const alive  = health === 'alive'
  const slow   = health === 'slow'
  const color  = alive ? '#00d48a' : slow ? '#f59e0b' : '#4d5a6e'
  const dot    = alive ? '●' : slow ? '◐' : '○'
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 5,
      background: 'rgba(255,255,255,0.03)',
      border: `1px solid ${color}22`,
      borderRadius: 4, padding: '3px 8px',
    }}>
      <span style={{ color, fontSize: 8 }}>{dot}</span>
      <span style={{ color: 'var(--muted)', fontSize: 8, letterSpacing: '0.08em', ...MONO }}>{name}</span>
    </div>
  )
}

function StatCard({ label, value, sub, color }: {
  label: string; value: string; sub: string; color: string
}) {
  return (
    <div style={{
      background: 'rgba(255,255,255,0.02)',
      border: '1px solid rgba(255,255,255,0.05)',
      borderRadius: 8, padding: '14px 16px',
    }}>
      <div style={{ color: 'var(--muted)', fontSize: 8, letterSpacing: '0.12em', marginBottom: 6, ...MONO }}>{label}</div>
      <div style={{ color, fontSize: 22, fontWeight: 800, lineHeight: 1, ...MONO }}>{value}</div>
      <div style={{ color: 'var(--dim)', fontSize: 8, marginTop: 6, ...MONO }}>{sub}</div>
    </div>
  )
}

// ── Extracted components ───────────────────────────────────────────────────

function NarrativeStrip({ nd }: { nd: NarrativeData | undefined }) {
  const cgCoins  = nd?.coingecko   ?? []
  const dexCoins = nd?.dexscreener ?? []
  const hasData  = cgCoins.length > 0 || dexCoins.length > 0
  const updAt    = nd?.updated_at
  const updLabel = updAt ? (() => {
    try {
      const ago = Math.floor((Date.now() - new Date(updAt).getTime()) / 60000)
      if (ago < 60) return `${ago}m ago`
      return `${Math.floor(ago / 60)}h ago`
    } catch { return '' }
  })() : null

  return (
    <div style={{
      background: 'rgba(255,255,255,0.015)',
      border: '1px solid rgba(255,255,255,0.05)',
      borderRadius: 8, padding: '10px 16px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: hasData ? 10 : 0 }}>
        <span style={{ ...MONO, color: 'var(--dim)', fontSize: 8, letterSpacing: '0.12em', flexShrink: 0 }}>
          🔥 NARRATIVE TRENDING
        </span>
        {updLabel && (
          <span style={{ ...MONO, color: 'var(--dim)', fontSize: 7 }}>
            updated {updLabel}
          </span>
        )}
        {!hasData && (
          <span style={{ ...MONO, color: 'var(--dim)', fontSize: 8 }}>
            collecting… refreshes every 4h via research loop
          </span>
        )}
      </div>

      {hasData && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          {/* CoinGecko coins */}
          {cgCoins.slice(0, 7).map(c => (
            <span key={`cg-${c.symbol}`} style={{
              ...MONO, fontSize: 8, fontWeight: 700,
              color: '#f59e0b',
              background: 'rgba(245,158,11,0.08)',
              border: '1px solid rgba(245,158,11,0.2)',
              borderRadius: 4, padding: '3px 7px',
              letterSpacing: '0.08em',
            }}>
              {c.symbol}
              <span style={{ color: '#f59e0b55', fontWeight: 400 }}> CG</span>
            </span>
          ))}

          {/* DexScreener Solana boosted */}
          {dexCoins.slice(0, 10).map(t => (
            <span key={`dex-${t.mint ?? t.symbol}`} style={{
              ...MONO, fontSize: 8, fontWeight: 700,
              color: '#00d48a',
              background: 'rgba(0,212,138,0.06)',
              border: '1px solid rgba(0,212,138,0.15)',
              borderRadius: 4, padding: '3px 7px',
              letterSpacing: '0.08em',
            }}>
              {t.symbol || t.mint?.slice(0, 6) + '…'}
              {t.boosts && t.boosts > 0 && (
                <span style={{ color: '#00d48a55', fontWeight: 400 }}> ×{t.boosts}</span>
              )}
              <span style={{ color: '#00d48a55', fontWeight: 400 }}> DEX</span>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Patch 182: Score threshold analysis panel ──────────────────────────────────

function ScoreAnalysisPanel({ sa }: { sa: ScoreAnalysis }) {
  const verdictColor = sa.verdict.label === 'MISALIGNED' ? '#ef4444'
    : sa.verdict.label === 'SUBOPTIMAL' ? '#f59e0b' : '#00d48a'

  const ow = sa.optimal_window
  const envT = sa.threshold_sim.find(t => t.is_current)
  const allT  = sa.threshold_sim.find(t => t.threshold === 0)

  return (
    <div style={{
      background: `${verdictColor}08`,
      border: `1px solid ${verdictColor}25`,
      borderRadius: 6, padding: '10px 14px', marginBottom: 14,
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ color: verdictColor, fontSize: 8 }}>◆</span>
          <span style={{ color: 'var(--muted)', fontSize: 8, letterSpacing: '0.1em', ...MONO }}>
            SCORE THRESHOLD ANALYSIS
          </span>
        </div>
        <span style={{
          color: verdictColor, fontSize: 8, ...MONO,
          background: `${verdictColor}15`, border: `1px solid ${verdictColor}30`,
          borderRadius: 3, padding: '2px 6px', fontWeight: 700,
        }}>
          {sa.verdict.label}
        </span>
      </div>

      {/* Verdict message */}
      <div style={{ color: '#8a9ab0', fontSize: 8, ...MONO, marginBottom: 10, lineHeight: 1.5 }}>
        {sa.verdict.message}
      </div>

      {/* Two-column layout: threshold sim + summary stats */}
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>

        {/* Threshold sim table */}
        <div style={{ flex: '1 1 200px' }}>
          <div style={{ color: 'var(--dim)', fontSize: 7, letterSpacing: '0.08em', ...MONO, marginBottom: 5 }}>
            THRESHOLD COMPARISON (24H OUTCOMES)
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse', ...MONO }}>
            <thead>
              <tr style={{ color: 'var(--dim)', fontSize: 7 }}>
                <th style={{ textAlign: 'left', paddingBottom: 3 }}>GATE</th>
                <th style={{ textAlign: 'right', paddingBottom: 3 }}>N</th>
                <th style={{ textAlign: 'right', paddingBottom: 3 }}>WR%</th>
                <th style={{ textAlign: 'right', paddingBottom: 3 }}>AVG24H</th>
              </tr>
            </thead>
            <tbody>
              {sa.threshold_sim.map(t => {
                const isCurrent = t.is_current
                const wrColor   = t.wr >= 45 ? '#00d48a' : t.wr >= 30 ? '#f59e0b' : '#ef4444'
                const retColor  = t.avg_24h >= 0 ? '#00d48a' : '#ef4444'
                return (
                  <tr key={t.threshold} style={{
                    borderTop: '1px solid rgba(255,255,255,0.03)',
                    background: isCurrent ? `${verdictColor}10` : 'transparent',
                  }}>
                    <td style={{ padding: '4px 0', fontSize: 9, color: isCurrent ? verdictColor : 'var(--muted)', fontWeight: isCurrent ? 700 : 400 }}>
                      ≥{t.threshold}{isCurrent ? ' ← ENV' : ''}
                    </td>
                    <td style={{ textAlign: 'right', padding: '4px 6px', fontSize: 9, color: 'var(--dim)' }}>{t.n}</td>
                    <td style={{ textAlign: 'right', padding: '4px 6px', fontSize: 10, fontWeight: 700, color: wrColor }}>{t.wr.toFixed(0)}%</td>
                    <td style={{ textAlign: 'right', padding: '4px 0', fontSize: 9, color: retColor }}>
                      {t.avg_24h >= 0 ? '+' : ''}{t.avg_24h.toFixed(1)}%
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        {/* Right column: optimal window + bought split */}
        <div style={{ flex: '0 1 180px', display: 'flex', flexDirection: 'column', gap: 10 }}>

          {/* Optimal window */}
          {ow && (
            <div>
              <div style={{ color: 'var(--dim)', fontSize: 7, letterSpacing: '0.08em', ...MONO, marginBottom: 5 }}>
                OPTIMAL SCORE WINDOW
              </div>
              <div style={{ ...MONO }}>
                <span style={{ color: '#00d48a', fontSize: 12, fontWeight: 700 }}>
                  {ow.lo}–{ow.hi}
                </span>
                <span style={{ color: 'var(--dim)', fontSize: 8 }}> score range</span>
              </div>
              <div style={{ color: '#8a9ab0', fontSize: 8, ...MONO, marginTop: 2 }}>
                n={ow.n} · WR <span style={{ color: '#00d48a' }}>{ow.wr.toFixed(0)}%</span>
                {' '}· avg <span style={{ color: ow.avg_24h >= 0 ? '#00d48a' : '#ef4444' }}>
                  {ow.avg_24h >= 0 ? '+' : ''}{ow.avg_24h.toFixed(1)}%
                </span>
              </div>
              {/* Patch 183: show multi-band or single-band tuner recommendation */}
              {sa.tuner?.multi_band_mode && sa.tuner.score_bands && sa.tuner.score_bands.length > 0 ? (
                <div style={{ marginTop: 6 }}>
                  <div style={{ color: 'var(--dim)', fontSize: 7, letterSpacing: '0.08em', ...MONO, marginBottom: 4 }}>
                    TUNER BANDS ({sa.tuner.optimization_horizon?.toUpperCase() ?? '24H'}-OPTIMIZED)
                  </div>
                  {sa.tuner.score_bands.map((b, i) => {
                    const bColor = b.wr >= 60 ? '#00d48a' : b.wr >= 50 ? '#f59e0b' : '#8a9ab0'
                    return (
                      <div key={i} style={{
                        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                        marginBottom: 3, padding: '3px 6px', borderRadius: 3,
                        background: i === 0 ? `${bColor}12` : 'transparent',
                        border: `1px solid ${bColor}${i === 0 ? '25' : '10'}`,
                      }}>
                        <span style={{ color: bColor, fontSize: 9, fontWeight: 700, ...MONO }}>
                          {b.lo}–{b.hi}
                        </span>
                        <span style={{ color: 'var(--dim)', fontSize: 7, ...MONO }}>
                          WR {b.wr.toFixed(0)}% · 24h {b.avg_24h >= 0 ? '+' : ''}{b.avg_24h.toFixed(1)}%
                        </span>
                      </div>
                    )
                  })}
                  <div style={{ color: '#4a6280', fontSize: 7, ...MONO, marginTop: 3 }}>
                    {sa.tuner.confidence} conf · {sa.tuner.sample_size} samples
                  </div>
                </div>
              ) : sa.tuner ? (
                <div style={{ color: 'var(--dim)', fontSize: 7, ...MONO, marginTop: 4 }}>
                  tuner: {sa.tuner.min_score}–{sa.tuner.max_score}
                  {' '}({sa.tuner.confidence} conf)
                </div>
              ) : null}
            </div>
          )}

          {/* Bought vs not-bought */}
          <div>
            <div style={{ color: 'var(--dim)', fontSize: 7, letterSpacing: '0.08em', ...MONO, marginBottom: 5 }}>
              BOUGHT vs SKIPPED
            </div>
            {(['bought', 'not_bought'] as const).map(k => {
              const row = sa.bought_split[k]
              const wrC = row.wr >= 40 ? '#00d48a' : row.wr >= 25 ? '#f59e0b' : '#ef4444'
              return (
                <div key={k} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                  <span style={{ color: 'var(--dim)', fontSize: 7, ...MONO }}>
                    {k === 'bought' ? 'BOUGHT' : 'SKIPPED'}
                    <span style={{ color: '#4a6280', marginLeft: 4 }}>n={row.n}</span>
                  </span>
                  <span style={{ color: wrC, fontSize: 9, fontWeight: 700, ...MONO }}>{row.wr.toFixed(0)}%</span>
                </div>
              )
            })}
          </div>

          {/* Env vs baseline comparison */}
          {envT && allT && (
            <div style={{ color: 'var(--dim)', fontSize: 7, ...MONO, borderTop: '1px solid rgba(255,255,255,0.04)', paddingTop: 6 }}>
              ENV gate vs no-gate:{' '}
              <span style={{ color: '#ef4444', fontWeight: 700 }}>
                {envT.wr.toFixed(0)}% vs {allT.wr.toFixed(0)}%
              </span>
            </div>
          )}
        </div>
      </div>

      {/* P184/P186: Horizon comparison — 4h vs 24h tuner optimization */}
      {sa.horizon_comparison && !sa.horizon_comparison.error && (() => {
        const hc = sa.horizon_comparison!
        const hvLabel  = hc.verdict.label
        const hvColor  = hvLabel === 'SWITCH_RECOMMENDED' ? '#f59e0b'
          : hvLabel === 'ALREADY_SWITCHED'  ? '#00d48a'
          : hvLabel === 'CURRENTLY_OPTIMAL' ? '#00d48a'
          : hvLabel === 'ALIGNED'           ? '#00d48a' : '#4a6280'
        const activeHz = hc.active_horizon?.toUpperCase() ?? null
        return (
          <div style={{
            marginTop: 12, borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: 10,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
              <span style={{ color: 'var(--dim)', fontSize: 7, letterSpacing: '0.08em', ...MONO }}>
                TUNER HORIZON COMPARISON · {hc.n_both} samples
                {activeHz && <span style={{ color: '#00d48a', marginLeft: 4 }}>· ACTIVE: {activeHz}</span>}
              </span>
              <span style={{
                color: hvColor, fontSize: 7, ...MONO, fontWeight: 700,
                background: `${hvColor}15`, border: `1px solid ${hvColor}30`,
                borderRadius: 3, padding: '2px 5px',
              }}>{hvLabel.replace(/_/g, ' ')}</span>
            </div>

            {/* Side-by-side band columns */}
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 6 }}>
              {(['4h', '24h'] as const).map(hz => {
                const bList = hz === '4h' ? hc.bands_4h : hc.bands_24h
                const hzColor = hz === '4h' ? '#8a9ab0' : '#00d48a'
                return (
                  <div key={hz} style={{ flex: '1 1 140px' }}>
                    <div style={{ color: hzColor, fontSize: 7, letterSpacing: '0.08em', ...MONO, marginBottom: 4 }}>
                      {hz.toUpperCase()} BANDS ({bList.length})
                    </div>
                    {bList.map((b, i) => {
                      const isMissed    = hz === '24h' && hc.bands_missed_by_4h.some(m => m.lo === b.lo && m.hi === b.hi)
                      const alreadyFixed = isMissed && hvLabel === 'ALREADY_SWITCHED'
                      const missColor   = alreadyFixed ? '#00d48a' : '#f59e0b'
                      const bColor      = b.wr >= 60 ? '#00d48a' : b.wr >= 50 ? '#f59e0b' : '#8a9ab0'
                      return (
                        <div key={i} style={{
                          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                          padding: '3px 6px', marginBottom: 2, borderRadius: 3,
                          background: isMissed ? `${missColor}10` : 'rgba(255,255,255,0.02)',
                          border: isMissed ? `1px solid ${missColor}30` : '1px solid transparent',
                        }}>
                          <span style={{ color: bColor, fontSize: 9, fontWeight: 700, ...MONO }}>
                            {b.lo}–{b.hi}
                            {isMissed && <span style={{ color: missColor, fontSize: 7, marginLeft: 3 }}>{alreadyFixed ? '✓' : '★'}</span>}
                          </span>
                          <span style={{ color: 'var(--dim)', fontSize: 7, ...MONO }}>
                            WR {b.wr.toFixed(0)}% · {hz === '4h' ? '4h' : '24h'} {b.avg_ret >= 0 ? '+' : ''}{b.avg_ret.toFixed(1)}%
                          </span>
                        </div>
                      )
                    })}
                  </div>
                )
              })}
            </div>

            {/* Verdict message */}
            <div style={{ color: hvColor, fontSize: 7, ...MONO, lineHeight: 1.5, opacity: 0.85 }}>
              {hc.verdict.message}
            </div>
          </div>
        )
      })()}
    </div>
  )
}

function LearningEngineStatus({ an, learnedT }: { an: AnalyticsData; learnedT: LearnedThresholds | null }) {
  const ab = an.auto_buy!
  // Effective score_min: use tuner learned value if medium/high confidence, else env baseline
  const effectiveScore = (learnedT && (learnedT.confidence === 'medium' || learnedT.confidence === 'high'))
    ? (learnedT.thresholds['min_score'] as number)
    : ab.score_min
  return (
    <div style={{
      background: 'rgba(255,255,255,0.02)',
      border: '1px solid rgba(255,255,255,0.06)',
      borderRadius: 8, padding: '12px 16px',
      display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap',
    }}>
      {/* Mode badges */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
        <span style={{
          ...MONO, fontSize: 9, fontWeight: 700, letterSpacing: '0.12em',
          padding: '3px 8px', borderRadius: 3,
          background: ab.dry_run ? 'rgba(245,158,11,0.12)' : 'rgba(0,212,138,0.12)',
          border: `1px solid ${ab.dry_run ? '#f59e0b' : '#00d48a'}44`,
          color: ab.dry_run ? '#f59e0b' : '#00d48a',
        }}>
          {ab.dry_run ? 'PAPER MODE' : 'LIVE MODE'}
        </span>
        <span style={{
          ...MONO, fontSize: 9, fontWeight: 700, letterSpacing: '0.12em',
          padding: '3px 8px', borderRadius: 3,
          background: ab.enabled ? 'rgba(0,212,138,0.08)' : 'rgba(77,90,110,0.10)',
          border: `1px solid ${ab.enabled ? '#00d48a' : '#2d4060'}44`,
          color: ab.enabled ? '#00d48a' : '#4d5a6e',
        }}>
          AUTO-BUY {ab.enabled ? 'ON' : 'OFF'}
        </span>
      </div>

      {/* Tuner progress bar — Patch 149: phase system */}
      <div style={{ flex: 1, minWidth: 180 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 5 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ ...MONO, color: 'var(--dim)', fontSize: 8, letterSpacing: '0.1em' }}>
              LEARNING LOOP
            </span>
            {an.phase_label && (
              <span style={{
                ...MONO, fontSize: 8, fontWeight: 700, letterSpacing: '0.1em',
                padding: '1px 5px', borderRadius: 3,
                background: an.phase === 4 ? 'rgba(0,212,138,0.10)' : an.phase === 3 ? 'rgba(245,158,11,0.10)' : 'rgba(96,165,250,0.08)',
                border: `1px solid ${an.phase === 4 ? 'rgba(0,212,138,0.3)' : an.phase === 3 ? 'rgba(245,158,11,0.3)' : 'rgba(96,165,250,0.2)'}`,
                color: an.phase === 4 ? '#00d48a' : an.phase === 3 ? '#f59e0b' : '#60a5fa',
              }}>
                {an.phase_label}
              </span>
            )}
          </div>
          <span style={{ ...MONO, fontSize: 8, color:
            learnedT?.confidence === 'high'   ? '#00d48a' :
            learnedT?.confidence === 'medium' ? '#f59e0b' :
            learnedT?.confidence === 'low'    ? '#f59e0b' : 'var(--muted)',
          }}>
            {an.complete} / {ab.tuner_threshold} outcomes
            {learnedT
              ? ` · ${learnedT.confidence.toUpperCase()} CONF`
              : an.complete >= 20 ? ' · LOW CONF' : ' · tuner pending'}
          </span>
        </div>
        <div style={{ height: 4, background: 'rgba(255,255,255,0.05)', borderRadius: 2, overflow: 'hidden' }}>
          <div style={{
            height: '100%',
            width: `${ab.complete_pct}%`,
            background: learnedT?.confidence === 'high' ? '#00d48a' : learnedT ? '#f59e0b' : 'rgba(90,120,160,0.45)',
            borderRadius: 2, transition: 'width 0.6s ease',
          }} />
        </div>
      </div>

      {/* Config chips */}
      <div style={{ display: 'flex', gap: 14, flexShrink: 0 }}>
        {([['BUY $', `$${ab.buy_usd}`], ['MAX OPEN', `${ab.max_open}`], ['SCORE MIN', `${effectiveScore}`]] as const).map(([lbl, val]) => (
          <div key={lbl} style={{ textAlign: 'center' }}>
            <div style={{ ...MONO, color: 'var(--dim)', fontSize: 7, letterSpacing: '0.1em', marginBottom: 2 }}>{lbl}</div>
            <div style={{ ...MONO, color: 'var(--dim)', fontSize: 11, fontWeight: 700 }}>{val}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function OpenPositionsTable({ positions, busyMints, onSell }: {
  positions: MemecoinPosition[]
  busyMints: Set<string>
  onSell: (pos: MemecoinPosition) => void
}) {
  return (
    <div style={{
      background: 'rgba(255,255,255,0.02)',
      border: '1px solid rgba(245,158,11,0.15)',
      borderRadius: 8, padding: '16px 18px',
    }}>
      <div style={{ color: '#f59e0b', fontSize: 9, letterSpacing: '0.1em', marginBottom: 14, ...MONO }}>
        ▶ OPEN POSITIONS ({positions.length})
      </div>
      <div className="pos-table-wrap">
      <table style={{ width: '100%', minWidth: 560, borderCollapse: 'collapse', ...MONO }}>
        <thead>
          <tr style={{ color: 'var(--muted)', fontSize: 9 }}>
            {['TOKEN', 'ENTRY', 'CURRENT', 'PNL', 'SIZE', 'OPENED', ''].map((h, i) => (
              <th key={i} style={{ textAlign: i === 0 ? 'left' : 'right', padding: `0 ${i === 6 ? 0 : 10}px 8px ${i === 0 ? 0 : 0}px` }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {positions.map(p => {
            const busy    = busyMints.has(p.mint)
            const pnlBar  = Math.min(100, Math.max(0, Math.abs(p.pnl_pct)))
            const barClr  = p.pnl_pct >= 0 ? '#00d48a' : '#ef4444'
            return (
              <tr key={p.id} style={{ borderTop: '1px solid rgba(255,255,255,0.04)' }}>
                <td style={{ padding: '12px 10px 12px 0' }}>
                  <span style={{ color: '#c0cfe0', fontWeight: 700, fontSize: 13 }}>{p.symbol}</span>
                  <span style={{ color: 'var(--dim)', fontSize: 8, marginLeft: 6 }}>#{p.id}</span>
                </td>
                <td style={{ textAlign: 'right', padding: '12px 10px', color: '#4d5a6e', fontSize: 11 }}>
                  {fmtPrice(p.entry_price)}
                </td>
                <td style={{ textAlign: 'right', padding: '12px 10px', color: '#8a9ab0', fontSize: 11 }}>
                  {fmtPrice(p.current_price)}
                </td>
                <td style={{ textAlign: 'right', padding: '12px 10px' }}>
                  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 3 }}>
                    <span style={{ color: barClr, fontWeight: 700, fontSize: 12 }}>
                      {p.pnl_pct >= 0 ? '+' : ''}{p.pnl_pct.toFixed(1)}%
                      <span style={{ color: barClr, fontSize: 9, marginLeft: 5, fontWeight: 400 }}>
                        (${p.pnl_usd >= 0 ? '+' : ''}{p.pnl_usd.toFixed(2)})
                      </span>
                    </span>
                    <div style={{ width: 56, height: 3, background: 'rgba(255,255,255,0.06)', borderRadius: 2 }}>
                      <div style={{ width: `${pnlBar}%`, height: '100%', background: barClr, borderRadius: 2 }} />
                    </div>
                  </div>
                </td>
                <td style={{ textAlign: 'right', padding: '12px 10px', color: '#4d5a6e', fontSize: 11 }}>
                  ${p.amount_usd.toFixed(0)}
                </td>
                <td style={{ textAlign: 'right', padding: '12px 10px', color: 'var(--dim)', fontSize: 9 }}>
                  {fmtRelTime(p.opened)}
                </td>
                <td style={{ textAlign: 'right', padding: '12px 0' }}>
                  <button
                    onClick={() => onSell(p)} disabled={busy}
                    style={{
                      background: busy ? 'rgba(239,68,68,0.03)' : 'rgba(239,68,68,0.09)',
                      border: '1px solid rgba(239,68,68,0.22)', borderRadius: 4,
                      color: busy ? 'var(--dim)' : '#ef4444',
                      cursor: busy ? 'default' : 'pointer',
                      ...MONO, fontSize: 9, padding: '5px 14px', fontWeight: 700,
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
  )
}

function ScannerSignalsPanel({ signals, isLoading, busyMints, buyAmounts, onBuy, onAmountChange }: {
  signals: MemecoinSignal[]
  isLoading: boolean
  busyMints: Set<string>
  buyAmounts: Record<string, string>
  onBuy: (signal: MemecoinSignal) => void
  onAmountChange: (mint: string, value: string) => void
}) {
  return (
    <div style={{
      background: 'rgba(255,255,255,0.02)',
      border: '1px solid rgba(255,255,255,0.05)',
      borderRadius: 8, padding: '16px 18px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
        <div style={{ color: 'var(--text2)', fontSize: 9, letterSpacing: '0.1em', ...MONO }}>
          SCANNER SIGNALS{signals.length > 0 ? ` (${signals.length})` : ''}
        </div>
        <div style={{ display: 'flex', gap: 16, ...MONO }}>
          {[
            `mcap $300k–$50M`,
            `age 1–30d`,
            `rug GOOD/WARN only`,
            `vol accel ≥5%`,
          ].map(t => (
            <span key={t} style={{ color: 'var(--muted)', fontSize: 8 }}>· {t}</span>
          ))}
        </div>
      </div>

      {isLoading ? (
        <div style={{ padding: '24px 0', color: 'var(--dim)', fontSize: 11, ...MONO }}>scanning…</div>
      ) : signals.length === 0 ? (
        <div style={{ padding: '24px 0', color: 'var(--dim)', fontSize: 11, ...MONO }}>
          no signals yet — scan runs every 5 min
        </div>
      ) : (
        <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' as any }}>
          <table style={{ width: '100%', minWidth: 700, borderCollapse: 'collapse', ...MONO }}>
            <thead>
              <tr style={{ color: 'var(--muted)', fontSize: 8, letterSpacing: '0.06em' }}>
                <th style={{ textAlign: 'left',  padding: '0 8px 10px 0'  }}>SAFETY</th>
                <th style={{ textAlign: 'left',  padding: '0 10px 10px 0' }}>TOKEN</th>
                <th style={{ textAlign: 'right', padding: '0 10px 10px'   }}>PRICE</th>
                <th style={{ textAlign: 'right', padding: '0 10px 10px'   }}>MCAP</th>
                <th style={{ textAlign: 'right', padding: '0 10px 10px'   }}>AGE</th>
                <th style={{ textAlign: 'right', padding: '0 10px 10px'   }}>SCORE</th>
                <th style={{ textAlign: 'right', padding: '0 10px 10px'   }}>1H ▲</th>
                <th style={{ textAlign: 'right', padding: '0 10px 10px'   }}>VACC</th>
                <th style={{ textAlign: 'right', padding: '0 10px 10px'   }}>BUYS</th>
                <th style={{ textAlign: 'right', padding: '0 10px 10px'   }}>TOP HLDR</th>
                <th style={{ textAlign: 'right', padding: '0 0 10px'      }}>BUY</th>
              </tr>
            </thead>
            <tbody>
              {signals.map(s => {
                const busy = busyMints.has(s.mint)
                const amt  = buyAmounts[s.mint] ?? '10'
                const sc   = scoreColor(s.score)
                const rc   = rugColor(s.rug_label)
                return (
                  <tr key={s.mint} style={{ borderTop: '1px solid rgba(255,255,255,0.04)' }}>

                    {/* Safety badge */}
                    <td style={{ padding: '12px 8px 12px 0' }}>
                      <div style={{
                        display: 'inline-flex', alignItems: 'center', gap: 4,
                        background: `${rc}11`,
                        border: `1px solid ${rc}33`,
                        borderRadius: 4, padding: '3px 7px',
                      }}>
                        <span style={{ fontSize: 8 }}>{rugEmoji(s.rug_label)}</span>
                        <span style={{ color: rc, fontSize: 8, fontWeight: 700 }}>{s.rug_label}</span>
                      </div>
                      {/* Sub-indicators */}
                      <div style={{ display: 'flex', gap: 4, marginTop: 4, flexWrap: 'wrap' }}>
                        {s.mint_revoked    && <span style={{ color: '#00d48a', fontSize: 7 }}>✓MINT</span>}
                        {s.freeze_revoked  && <span style={{ color: '#00d48a', fontSize: 7 }}>✓FREEZE</span>}
                        {s.lp_locked_pct > 50 && (
                          <span style={{ color: '#7c9fd4', fontSize: 7 }}>LP{s.lp_locked_pct.toFixed(0)}%</span>
                        )}
                      </div>
                    </td>

                    {/* Token */}
                    <td style={{ padding: '12px 10px 12px 0' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                        <a
                          href={s.dex_url} target="_blank" rel="noreferrer"
                          style={{ color: '#c0cfe0', textDecoration: 'none', fontWeight: 700, fontSize: 13 }}
                        >
                          {s.symbol}
                        </a>
                        {s.narrative && (
                          <span title={`Trending on: ${(s.narrative_sources ?? []).join(', ')}`}
                            style={{ fontSize: 10 }}>🔥</span>
                        )}
                      </div>
                      <div style={{ color: 'var(--dim)', fontSize: 8, marginTop: 2 }}>
                        {s.mint.slice(0, 8)}…
                      </div>
                    </td>

                    {/* Price */}
                    <td style={{ textAlign: 'right', padding: '12px 10px', color: '#8a9ab0', fontSize: 11 }}>
                      {fmtPrice(s.price)}
                    </td>

                    {/* Mcap */}
                    <td style={{ textAlign: 'right', padding: '12px 10px' }}>
                      <span style={{ color: '#7c9fd4', fontSize: 11 }}>{fmtMcap(s.mcap_usd)}</span>
                    </td>

                    {/* Age */}
                    <td style={{ textAlign: 'right', padding: '12px 10px', color: '#4d5a6e', fontSize: 11 }}>
                      {fmtAge(s.token_age_days)}
                    </td>

                    {/* Score */}
                    <td style={{ textAlign: 'right', padding: '12px 10px' }}>
                      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 3 }}>
                        <span style={{ color: sc, fontWeight: 800, fontSize: 14 }}>{s.score}</span>
                        <div style={{ width: 40, height: 3, background: 'rgba(255,255,255,0.06)', borderRadius: 2 }}>
                          <div style={{ width: `${s.score}%`, height: '100%', background: sc, borderRadius: 2 }} />
                        </div>
                      </div>
                    </td>

                    {/* 1H */}
                    <td style={{
                      textAlign: 'right', padding: '12px 10px', fontWeight: 700, fontSize: 12,
                      color: (s.change_1h ?? 0) >= 0 ? '#00d48a' : '#ef4444',
                    }}>
                      {s.change_1h != null ? `${s.change_1h >= 0 ? '+' : ''}${s.change_1h.toFixed(1)}%` : '—'}
                    </td>

                    {/* Vol acceleration */}
                    <td style={{ textAlign: 'right', padding: '12px 10px' }}>
                      <span style={{
                        color: s.vol_acceleration >= 20 ? '#00d48a' : s.vol_acceleration >= 10 ? '#f59e0b' : '#4d5a6e',
                        fontSize: 11, fontWeight: s.vol_acceleration >= 20 ? 700 : 400,
                      }}>
                        {s.vol_acceleration.toFixed(0)}%
                      </span>
                      <div style={{ color: 'var(--dim)', fontSize: 7, marginTop: 1 }}>of daily</div>
                    </td>

                    {/* Buy pressure */}
                    <td style={{ textAlign: 'right', padding: '12px 10px' }}>
                      {s.buy_pressure != null ? (
                        <span style={{
                          color: s.buy_pressure > 60 ? '#00d48a' : s.buy_pressure < 40 ? '#ef4444' : '#a0aec0',
                          fontSize: 11,
                          fontWeight: s.buy_pressure > 60 || s.buy_pressure < 40 ? 700 : 400,
                        }}>
                          {s.buy_pressure.toFixed(0)}%
                        </span>
                      ) : (
                        <span style={{ color: 'var(--dim)' }}>—</span>
                      )}
                      <div style={{ color: 'var(--dim)', fontSize: 7, marginTop: 1 }}>1h buys</div>
                    </td>

                    {/* Top holder */}
                    <td style={{ textAlign: 'right', padding: '12px 10px' }}>
                      <span style={{
                        color: s.top_holder_pct > 20 ? '#f59e0b' : s.top_holder_pct > 35 ? '#ef4444' : '#4d5a6e',
                        fontSize: 11,
                      }}>
                        {s.top_holder_pct > 0 ? `${s.top_holder_pct.toFixed(1)}%` : '—'}
                      </span>
                    </td>

                    {/* Buy */}
                    <td style={{ textAlign: 'right', padding: '12px 0' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 5, justifyContent: 'flex-end' }}>
                        <span style={{ color: 'var(--dim)', fontSize: 9 }}>$</span>
                        <input
                          type="number" value={amt} min="1"
                          onChange={e => onAmountChange(s.mint, e.target.value)}
                          style={{
                            width: 44, background: 'rgba(255,255,255,0.04)',
                            border: '1px solid rgba(255,255,255,0.08)',
                            borderRadius: 3, color: '#8a9ab0',
                            fontSize: 9, padding: '3px 5px', textAlign: 'right',
                            fontFamily: 'JetBrains Mono, monospace',
                          }}
                        />
                        <button
                          onClick={() => onBuy(s)} disabled={busy}
                          style={{
                            background: busy ? 'rgba(0,212,138,0.04)' : 'rgba(0,212,138,0.11)',
                            border: '1px solid rgba(0,212,138,0.28)', borderRadius: 4,
                            color: busy ? 'var(--dim)' : '#00d48a',
                            cursor: busy ? 'default' : 'pointer',
                            ...MONO, fontSize: 9, padding: '5px 14px', fontWeight: 700,
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
  )
}

function LearningSystem({ an, learnedT }: { an: AnalyticsData | undefined; learnedT: LearnedThresholds | null }) {
  // Patch 182: score threshold analysis — hook lives here so JSX below can access it
  const scoreAnalysisQuery = useQuery<ScoreAnalysis>({
    queryKey: ['memecoins-score-analysis'],
    queryFn:  async () => (await api.get('/memecoins/score-analysis')).data,
    refetchInterval: 600_000,
  })

  return (
    <div style={{
      background: 'rgba(255,255,255,0.02)',
      border: '1px solid rgba(255,255,255,0.05)',
      borderRadius: 8, padding: '16px 18px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
        <div style={{ color: 'var(--text2)', fontSize: 9, letterSpacing: '0.1em', ...MONO }}>
          LEARNING SYSTEM
        </div>
        {an && (
          <div style={{ color: 'var(--dim)', fontSize: 8, ...MONO }}>
            {an.total_tracked} tracked · {an.complete} complete · {an.pending} pending · {an.bought_count} bought
          </div>
        )}
      </div>

      {(!an || an.total_tracked === 0) ? (
        <div style={{ color: 'var(--dim)', fontSize: 9, ...MONO }}>
          collecting data — 1h/4h/24h returns fill in automatically · need 20 complete to tune
        </div>
      ) : (
        <>
          {/* Score bucket cards */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 10, marginBottom: 14 }}>
            {an.score_buckets.map(b => {
              const wr     = b.win_rate_4h
              const r4     = b.avg_return_4h
              const wrClr  = wr === null ? 'var(--dim)' : wr >= 55 ? '#00d48a' : wr >= 40 ? '#f59e0b' : '#ef4444'
              const r4Clr  = r4 === null ? 'var(--dim)' : r4 >= 0 ? '#00d48a' : '#ef4444'
              return (
                <div key={b.label} style={{
                  background: 'rgba(255,255,255,0.02)',
                  border: '1px solid rgba(255,255,255,0.05)',
                  borderRadius: 6, padding: '12px 14px',
                }}>
                  <div style={{ color: 'var(--muted)', fontSize: 8, letterSpacing: '0.1em', marginBottom: 8, ...MONO }}>
                    SCORE {b.label}
                  </div>
                  {b.count === 0 ? (
                    <div style={{ color: 'var(--dim)', fontSize: 9, ...MONO }}>no data yet</div>
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span style={{ color: 'var(--muted)', fontSize: 8, ...MONO }}>WIN RATE 4H</span>
                        <span style={{ color: wrClr, fontWeight: 700, fontSize: 13, ...MONO }}>
                          {wr !== null ? `${wr.toFixed(0)}%` : '—'}
                        </span>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span style={{ color: 'var(--muted)', fontSize: 8, ...MONO }}>AVG 4H</span>
                        <span style={{ color: r4Clr, fontWeight: 700, fontSize: 11, ...MONO }}>
                          {r4 !== null ? `${r4 >= 0 ? '+' : ''}${r4.toFixed(1)}%` : '—'}
                        </span>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span style={{ color: 'var(--dim)', fontSize: 8, ...MONO }}>AVG 24H</span>
                        <span style={{ color: b.avg_return_24h !== null ? pnlColor(b.avg_return_24h) : 'var(--dim)', fontSize: 10, ...MONO }}>
                          {b.avg_return_24h !== null ? `${b.avg_return_24h >= 0 ? '+' : ''}${b.avg_return_24h.toFixed(1)}%` : '—'}
                        </span>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span style={{ color: 'var(--dim)', fontSize: 8, ...MONO }}>SIGNALS / BUY RATE</span>
                        <span style={{ color: '#4d5a6e', fontSize: 9, ...MONO }}>
                          {b.count} · {b.buy_rate !== null ? `${b.buy_rate.toFixed(0)}%` : '—'}
                        </span>
                      </div>
                      {/* Win rate bar */}
                      {wr !== null && (
                        <div style={{ width: '100%', height: 3, background: 'rgba(255,255,255,0.06)', borderRadius: 2, marginTop: 2 }}>
                          <div style={{ width: `${wr}%`, height: '100%', background: wrClr, borderRadius: 2, transition: 'width 0.5s' }} />
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>

          {/* Rug label breakdown */}
          {an.rug_breakdown.length > 0 && (
            <div style={{ marginBottom: 14 }}>
              <div style={{ color: 'var(--muted)', fontSize: 8, letterSpacing: '0.1em', marginBottom: 8, ...MONO }}>
                SAFETY LABEL PERFORMANCE
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: `repeat(${an.rug_breakdown.length}, 1fr)`, gap: 8 }}>
                {an.rug_breakdown.map(rb => {
                  const rc  = rugColor(rb.label)
                  const r4c = rb.avg_return_4h !== null ? pnlColor(rb.avg_return_4h) : 'var(--dim)'
                  return (
                    <div key={rb.label} style={{
                      background: `${rc}08`,
                      border: `1px solid ${rc}22`,
                      borderRadius: 6, padding: '10px 12px',
                      display: 'flex', flexDirection: 'column', gap: 4,
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginBottom: 4 }}>
                        <span style={{ fontSize: 10 }}>{rugEmoji(rb.label)}</span>
                        <span style={{ color: rc, fontSize: 9, fontWeight: 700, ...MONO }}>{rb.label}</span>
                        <span style={{ color: 'var(--dim)', fontSize: 8, ...MONO }}>({rb.count})</span>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span style={{ color: 'var(--muted)', fontSize: 8, ...MONO }}>WIN 4H</span>
                        <span style={{ color: rb.win_rate_4h !== null ? (rb.win_rate_4h >= 50 ? '#00d48a' : '#f59e0b') : 'var(--dim)', fontSize: 11, fontWeight: 700, ...MONO }}>
                          {rb.win_rate_4h !== null ? `${rb.win_rate_4h.toFixed(0)}%` : '—'}
                        </span>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span style={{ color: 'var(--muted)', fontSize: 8, ...MONO }}>AVG 4H</span>
                        <span style={{ color: r4c, fontSize: 10, ...MONO }}>
                          {rb.avg_return_4h !== null ? `${rb.avg_return_4h >= 0 ? '+' : ''}${rb.avg_return_4h.toFixed(1)}%` : '—'}
                        </span>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Auto-tuned thresholds */}
          {learnedT && (
            <div style={{
              background: 'rgba(255,255,255,0.01)',
              border: `1px solid ${confidenceColor(learnedT.confidence)}18`,
              borderRadius: 6, padding: '10px 14px', marginBottom: 14,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ color: confidenceColor(learnedT.confidence), fontSize: 8 }}>◆</span>
                  <span style={{ color: 'var(--muted)', fontSize: 8, letterSpacing: '0.1em', ...MONO }}>
                    AUTO-TUNED THRESHOLDS
                  </span>
                </div>
                <span style={{
                  color: confidenceColor(learnedT.confidence), fontSize: 8, ...MONO,
                  background: `${confidenceColor(learnedT.confidence)}15`,
                  border: `1px solid ${confidenceColor(learnedT.confidence)}30`,
                  borderRadius: 3, padding: '2px 6px',
                }}>
                  {learnedT.confidence.toUpperCase()} CONFIDENCE · {learnedT.sample_size} samples
                </span>
              </div>
              <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap' }}>
                {Object.entries(learnedT.thresholds).map(([k, v]) => (
                  <div key={k} style={{ ...MONO }}>
                    <span style={{ color: 'var(--dim)', fontSize: 8 }}>{k.replace(/_/g, ' ')} </span>
                    <span style={{ color: '#7c9fd4', fontSize: 10, fontWeight: 700 }}>
                      {typeof v === 'number' ? (k.includes('pct') || k.includes('accel') ? `${v}%` : v) : String(v)}
                    </span>
                  </div>
                ))}
              </div>
              <div style={{ color: 'var(--dim)', fontSize: 7, marginTop: 6, ...MONO }}>
                updated {fmtRelTime(learnedT.updated_at)} · overall win rate {learnedT.win_rate.toFixed(0)}%
              </div>
            </div>
          )}

          {/* Patch 182: Score threshold analysis — surfaces threshold mismatch */}
          {scoreAnalysisQuery.data?.verdict && (
            <ScoreAnalysisPanel sa={scoreAnalysisQuery.data} />
          )}

          {/* Top performers table */}
          {an.top_performers.length > 0 && (
            <>
              <div style={{ color: 'var(--muted)', fontSize: 8, letterSpacing: '0.1em', marginBottom: 8, ...MONO }}>
                TOP PERFORMERS (4H RETURN)
              </div>
              <div className="pos-table-wrap">
              <table style={{ width: '100%', minWidth: 560, borderCollapse: 'collapse', ...MONO }}>
                <thead>
                  <tr style={{ color: 'var(--muted)', fontSize: 8 }}>
                    {['TOKEN', 'SAFETY', 'MCAP', 'AGE', 'SCORE', '1H AT SCAN', '4H', '24H', 'BOUGHT'].map((h, i) => (
                      <th key={h} style={{ textAlign: i === 0 ? 'left' : 'right', padding: `0 ${i === 8 ? 0 : 8}px 6px ${i === 0 ? 0 : 0}px` }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {an.top_performers.map((p, i) => (
                    <tr key={i} style={{ borderTop: '1px solid rgba(255,255,255,0.03)' }}>
                      <td style={{ padding: '7px 8px 7px 0', color: '#8a9ab0', fontWeight: 700, fontSize: 11 }}>{p.symbol}</td>
                      <td style={{ textAlign: 'right', padding: '7px 8px' }}>
                        <span style={{ color: rugColor(p.rug_label), fontSize: 9 }}>{rugEmoji(p.rug_label)} {p.rug_label}</span>
                      </td>
                      <td style={{ textAlign: 'right', padding: '7px 8px', color: '#4d5a6e', fontSize: 9 }}>
                        {p.mcap_at_scan ? fmtMcap(p.mcap_at_scan) : '—'}
                      </td>
                      <td style={{ textAlign: 'right', padding: '7px 8px', color: '#4d5a6e', fontSize: 9 }}>
                        {p.token_age_days ? fmtAge(p.token_age_days) : '—'}
                      </td>
                      <td style={{ textAlign: 'right', padding: '7px 8px', color: scoreColor(p.score), fontSize: 11, fontWeight: 700 }}>
                        {p.score}
                      </td>
                      <td style={{ textAlign: 'right', padding: '7px 8px', fontSize: 9, color: '#4d5a6e' }}>
                        {p.vol_acceleration != null ? `${p.vol_acceleration.toFixed(0)}%` : '—'}
                      </td>
                      <td style={{ textAlign: 'right', padding: '7px 8px', fontSize: 11, fontWeight: 700,
                        color: p.return_4h_pct !== null ? pnlColor(p.return_4h_pct) : 'var(--dim)' }}>
                        {p.return_4h_pct !== null ? `${p.return_4h_pct >= 0 ? '+' : ''}${p.return_4h_pct.toFixed(1)}%` : '—'}
                      </td>
                      <td style={{ textAlign: 'right', padding: '7px 8px', fontSize: 10,
                        color: p.return_24h_pct !== null ? pnlColor(p.return_24h_pct) : 'var(--dim)' }}>
                        {p.return_24h_pct !== null ? `${p.return_24h_pct >= 0 ? '+' : ''}${p.return_24h_pct.toFixed(1)}%` : '—'}
                      </td>
                      <td style={{ textAlign: 'right', padding: '7px 0', fontSize: 9,
                        color: p.bought ? '#00d48a' : 'var(--dim)' }}>
                        {p.bought ? '✓ YES' : 'passed'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            </>
          )}
        </>
      )}
    </div>
  )
}

// ── MemecoinsPage ──────────────────────────────────────────────────────────

export function MemecoinsPage() {
  const queryClient                = useQueryClient()
  const [buyAmounts, setBuyAmounts] = useState<Record<string, string>>({})
  const [busyMints,  setBusyMints]  = useState<Set<string>>(new Set())
  const [msg, setMsg]               = useState<{ text: string; ok: boolean } | null>(null)
  const [elapsed, setElapsed]       = useState(0)
  const lastFetchRef                = useRef<number>(Date.now())

  const { data, isLoading, dataUpdatedAt } = useQuery<MemecoinsStatus>({
    queryKey: ['memecoins'],
    queryFn:  async () => (await api.get('/memecoins/status')).data,
    refetchInterval: 30_000,
  })

  const analyticsQuery = useQuery<AnalyticsData>({
    queryKey: ['memecoins-analytics'],
    queryFn:  async () => (await api.get('/memecoins/analytics')).data,
    refetchInterval: 120_000,
  })

  const narrativeQuery = useQuery<NarrativeData>({
    queryKey: ['memecoins-trending'],
    queryFn:  async () => (await api.get('/memecoins/trending')).data,
    refetchInterval: 300_000,   // refresh every 5 min (backed by 4h server cache)
  })

  // Countdown timer
  useEffect(() => {
    if (dataUpdatedAt) lastFetchRef.current = dataUpdatedAt
  }, [dataUpdatedAt])

  useEffect(() => {
    const id = setInterval(() => {
      setElapsed(Math.floor((Date.now() - lastFetchRef.current) / 1000))
    }, 1000)
    return () => clearInterval(id)
  }, [])

  const signals      = data?.signals       ?? []
  const positions    = data?.positions     ?? []
  const stats        = data?.stats         ?? { win_rate: 0, total_pnl: 0, closed_count: 0 }
  const recentClosed = data?.recent_closed ?? []
  const learnedT     = data?.learned_thresholds ?? null

  const an = analyticsQuery.data
  const nd = narrativeQuery.data

  function flash(text: string, ok = true) {
    setMsg({ text, ok })
    setTimeout(() => setMsg(null), 4500)
  }

  async function handleBuy(signal: MemecoinSignal) {
    const amt = parseFloat(buyAmounts[signal.mint] ?? '10')
    if (!amt || amt <= 0) return
    setBusyMints(s => new Set(s).add(signal.mint))
    try {
      const r = await api.post('/memecoins/buy', {
        mint: signal.mint, symbol: signal.symbol, amount_usd: amt,
      })
      if (r.data?.success) {
        flash(`Bought ${signal.symbol} — $${amt}`)
        queryClient.invalidateQueries({ queryKey: ['memecoins'] })
      } else {
        flash(r.data?.error ?? 'Buy failed', false)
      }
    } catch (e: any) {
      flash(e?.response?.data?.detail ?? e.message, false)
    }
    setBusyMints(s => { const n = new Set(s); n.delete(signal.mint); return n })
  }

  async function handleSell(pos: MemecoinPosition) {
    const sign = pos.pnl_pct >= 0 ? '+' : ''
    if (!confirm(`Sell ${pos.symbol}? PnL: ${sign}${pos.pnl_pct.toFixed(1)}%`)) return
    setBusyMints(s => new Set(s).add(pos.mint))
    try {
      const r = await api.post(`/memecoins/sell/${pos.mint}`)
      if (r.data?.success) {
        const s = r.data.pnl_pct >= 0 ? '+' : ''
        flash(`Sold ${pos.symbol} — ${s}${r.data.pnl_pct.toFixed(1)}% ($${s}${r.data.pnl_usd.toFixed(2)})`)
        queryClient.invalidateQueries({ queryKey: ['memecoins'] })
      } else {
        flash(r.data?.error ?? 'Sell failed', false)
      }
    } catch (e: any) {
      flash(e?.response?.data?.detail ?? e.message, false)
    }
    setBusyMints(s => { const n = new Set(s); n.delete(pos.mint); return n })
  }

  const nextScan = Math.max(0, 300 - elapsed)
  const nextMm   = String(Math.floor(nextScan / 60)).padStart(1, '0')
  const nextSs   = String(nextScan % 60).padStart(2, '0')

  // Avg 4h return from analytics
  const all4hReturns = an?.score_buckets
    .flatMap(b => b.avg_return_4h !== null ? [b.avg_return_4h] : []) ?? []
  const avg4h = all4hReturns.length
    ? all4hReturns.reduce((a, b) => a + b, 0) / all4hReturns.length
    : null

  return (
    <div style={{ maxWidth: 1280, margin: '0 auto', padding: '20px', display: 'flex', flexDirection: 'column', gap: 14 }}>

      {/* ── Header ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <span style={{
          color: '#60a5fa', fontFamily: 'JetBrains Mono, monospace',
          fontWeight: 700, fontSize: 13, letterSpacing: '0.14em',
        }}>
          MEMECOIN SCAN
        </span>
        {learnedT ? (
          <span className="badge" style={{
            color: confidenceColor(learnedT.confidence),
            background: `${confidenceColor(learnedT.confidence)}18`,
            border: `1px solid ${confidenceColor(learnedT.confidence)}44`,
            fontSize: 9,
          }}>
            {learnedT.confidence.toUpperCase()} CONF
          </span>
        ) : (
          <span className="badge" style={{
            color: 'var(--amber)', background: 'rgba(245,158,11,0.1)',
            border: '1px solid rgba(245,158,11,0.25)', fontSize: 9,
          }}>
            PAPER
          </span>
        )}
        <span style={{ color: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>
          SOLANA · DexScreener + RUGCheck · 2× TP · −50% SL
        </span>
        {/* Right stats */}
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ color: 'var(--dim)', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}>next scan</span>
            <span style={{
              color: nextScan < 30 ? 'var(--amber)' : 'var(--text2)',
              fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, fontSize: 13, transition: 'color 0.3s',
            }}>
              {nextMm}:{nextSs}
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ color: 'var(--dim)', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}>signals</span>
            <span style={{
              color: signals.length > 0 ? 'var(--green)' : 'var(--dim)',
              fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, fontSize: 12,
            }}>
              {signals.length}
            </span>
          </div>
          {stats.closed_count > 0 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ color: 'var(--dim)', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}>win rate</span>
              <span style={{
                color: stats.win_rate >= 40 ? 'var(--green)' : 'var(--amber)',
                fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, fontSize: 12,
              }}>
                {stats.win_rate.toFixed(0)}%
              </span>
            </div>
          )}
          <AgentBadge name="memecoin_scan"    health="alive" />
          <AgentBadge name="memecoin_monitor" health="alive" />
        </div>
      </div>

      {/* ── Learning Engine Status — Patch 125 ── */}
      {an && an.auto_buy && (<LearningEngineStatus an={an} learnedT={learnedT} />)}

      {/* ── Narrative Momentum Strip — Patch 127 ── */}
      <NarrativeStrip nd={nd} />

      {/* ── Stats cards ── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 10 }}>
        <StatCard
          label="WIN RATE"
          value={`${stats.win_rate.toFixed(0)}%`}
          color={stats.win_rate >= 50 ? '#00d48a' : '#f59e0b'}
          sub={stats.closed_count > 0 ? `${stats.closed_count} trades closed` : 'no closed trades yet'}
        />
        <StatCard
          label="AVG 4H RETURN"
          value={avg4h !== null ? `${avg4h >= 0 ? '+' : ''}${avg4h.toFixed(1)}%` : '—'}
          color={avg4h === null ? 'var(--dim)' : avg4h >= 0 ? '#00d48a' : '#ef4444'}
          sub={an && an.complete > 0 ? `from ${an.complete} tracked signals` : 'collecting data…'}
        />
        <StatCard
          label="TOTAL PnL"
          value={`${stats.total_pnl >= 0 ? '+' : ''}$${stats.total_pnl.toFixed(2)}`}
          color={stats.total_pnl >= 0 ? '#00d48a' : '#ef4444'}
          sub="all-time realized"
        />
        <StatCard
          label="OPEN"
          value={String(positions.length)}
          color={positions.length > 0 ? '#f59e0b' : 'var(--dim)'}
          sub={positions.length > 0
            ? `$${positions.reduce((s, p) => s + p.amount_usd, 0).toFixed(0)} deployed`
            : 'no open positions'}
        />
      </div>

      {/* ── Flash ── */}
      {msg && (
        <div style={{
          padding: '10px 14px',
          background: msg.ok ? 'rgba(0,212,138,0.07)' : 'rgba(239,68,68,0.07)',
          border: `1px solid ${msg.ok ? 'rgba(0,212,138,0.2)' : 'rgba(239,68,68,0.2)'}`,
          borderRadius: 6, fontSize: 11, ...MONO,
          color: msg.ok ? '#00d48a' : '#ef4444',
        }}>
          {msg.ok ? '✅' : '❌'} {msg.text}
        </div>
      )}

      {/* ── Open Positions ── */}
      {positions.length > 0 && (<OpenPositionsTable positions={positions} busyMints={busyMints} onSell={handleSell} />)}

      {/* ── Scanner Signals ── */}
      <ScannerSignalsPanel
        signals={signals}
        isLoading={isLoading}
        busyMints={busyMints}
        buyAmounts={buyAmounts}
        onBuy={handleBuy}
        onAmountChange={(mint, val) => setBuyAmounts(prev => ({ ...prev, [mint]: val }))}
      />

      {/* ── Learning System ── */}
      <LearningSystem an={an} learnedT={learnedT} />

      {/* ── Recent Closed Trades ── */}
      {recentClosed.length > 0 && (
        <div style={{
          background: 'rgba(255,255,255,0.015)',
          border: '1px solid rgba(255,255,255,0.04)',
          borderRadius: 8, padding: '14px 18px',
        }}>
          <div style={{ color: 'var(--muted)', fontSize: 9, letterSpacing: '0.1em', marginBottom: 12, ...MONO }}>
            RECENT CLOSED
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {recentClosed.map((t, i) => {
              const clr = pnlColor(t.pnl_pct)
              const sign = t.pnl_pct >= 0 ? '+' : ''
              const reasonClr = t.exit_reason === 'TP_2X' ? '#00d48a' :
                                t.exit_reason === 'SL_50' ? '#ef4444' : '#4d5a6e'
              return (
                <div key={i} style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  padding: '8px 0',
                  borderTop: i > 0 ? '1px solid rgba(255,255,255,0.03)' : 'none',
                  ...MONO,
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <span style={{ color: '#c0cfe0', fontWeight: 700, fontSize: 12, minWidth: 60 }}>{t.symbol}</span>
                    <span style={{
                      color: reasonClr, fontSize: 8,
                      background: `${reasonClr}12`,
                      border: `1px solid ${reasonClr}28`,
                      borderRadius: 3, padding: '2px 6px',
                    }}>
                      {t.exit_reason}
                    </span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                    <span style={{ color: clr, fontWeight: 700, fontSize: 12 }}>
                      {sign}{t.pnl_pct.toFixed(1)}%
                    </span>
                    <span style={{ color: clr, fontSize: 10 }}>
                      ${sign}{t.pnl_usd.toFixed(2)}
                    </span>
                    <span style={{ color: 'var(--dim)', fontSize: 9 }}>
                      {fmtRelTime(t.closed_at)}
                    </span>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* ── Footer ── */}
      <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', paddingTop: 4 }}>
        {[
          '2× TP auto-exit',
          '−50% SL auto-exit',
          'RugCheck safety filter',
          'mcap $300k–$50M',
          'age 1–30 days',
          'semi-auto · you click BUY',
          'learning loop active',
        ].map(t => (
          <span key={t} style={{ color: 'var(--dim)', fontSize: 9, ...MONO }}>· {t}</span>
        ))}
      </div>

    </div>
  )
}
