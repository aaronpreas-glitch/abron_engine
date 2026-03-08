import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api'

// ── Types ────────────────────────────────────────────────────────────────────

interface SpotHolding {
  symbol:           string
  name:             string
  mint:             string
  target_pct:       number
  current_pct:      number
  token_amount:     number
  total_invested:   number
  avg_cost_usd:     number
  current_price:    number
  current_value:    number
  pnl_usd:          number
  pnl_pct:          number
  last_buy_ts:      string | null
  // Patch 130 — trend signal fields
  trend?:           'UPTREND' | 'DOWNTREND' | 'NEUTRAL'
  price_change_24h?: number | null
  price_change_6h?:  number | null
}

interface SpotStatus {
  holdings:        SpotHolding[]
  holdings_count:  number
  total_invested:  number
  total_value:     number
  total_pnl_usd:   number
  total_pnl_pct:   number
  dry_run:         boolean
  prices_ts:       string
}

interface AdviceItem {
  symbol:        string
  name:          string
  mint:          string
  target_pct:    number
  current_pct:   number
  gap_pct:       number
  suggested_usd: number
  current_price: number
}

interface SpotTx {
  id:           number
  ts_utc:       string
  symbol:       string
  side:         string
  amount_usd:   number
  token_amount: number
  price_usd:    number
  tx_sig:       string | null
  dry_run:      number
}

// Patch 134 — Analytics
interface SignalStat {
  label:          string
  count:          number
  win_rate_7d:    number | null
  avg_return_7d:  number | null
}

interface TokenStat {
  symbol:         string
  count:          number
  win_rate_7d:    number | null
  avg_return_7d:  number | null
}

interface RecentSignal {
  id:              number
  ts_utc:          string
  symbol:          string
  score:           number
  signal_type:     string
  price_at_signal: number | null
  h24_at_signal:   number | null
  h6_at_signal:    number | null
  fg_at_signal:    number | null
  trend_at_signal: string | null
  portfolio_gap:   number | null
  return_7d_pct:   number | null
  return_30d_pct:  number | null
  status:          string
}

interface SpotAnalytics {
  total:            number
  complete:         number
  pending:          number
  signal_breakdown: SignalStat[]
  fg_breakdown:     SignalStat[]
  token_breakdown:  TokenStat[]
  tuner:            { min_score: number; confidence: string; win_rate: number; sample_size: number; updated_at: string } | null
  recent_signals:   RecentSignal[]
}

// Patch 134 — DCA signal types
interface SpotSignal {
  score:       number
  signal_type: 'DCA_NOW' | 'WATCH' | 'HOLD' | 'AVOID'
  h24:         number | null
  h6:          number | null
  fg:          number | null
  trend:       string
  gap:         number
  price:       number
}

interface SpotSignalsResponse {
  signals:            Record<string, SpotSignal>
  signals_updated_at: string | null
  learning: {
    total:           number
    complete:        number
    tuner_threshold: number
    complete_pct:    number
    confidence:      string
    min_score:       number
    win_rate:        number | null
    sample_size:     number | null
  }
}

// ── Formatting helpers ────────────────────────────────────────────────────────

const MONO: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }

function fPrice(p: number): string {
  if (!p) return '—'
  if (p >= 1000)  return `$${p.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
  if (p >= 1)     return `$${p.toFixed(4)}`
  if (p >= 0.001) return `$${p.toFixed(6)}`
  return `$${p.toExponential(3)}`
}

function fUsd(v: number, sign = false): string {
  const prefix = sign && v > 0 ? '+' : ''
  return `${prefix}$${Math.abs(v).toFixed(2)}`
}

function fPct(v: number): string {
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
}

function pnlCol(v: number): string {
  if (v > 0) return '#00d48a'
  if (v < 0) return '#ef4444'
  return '#4d5a6e'
}

function relTime(ts: string | null): string {
  if (!ts) return '—'
  try {
    const ago = (Date.now() - new Date(ts.includes('T') ? ts : ts + 'Z').getTime()) / 1000
    if (ago < 60)    return `${Math.floor(ago)}s ago`
    if (ago < 3600)  return `${Math.floor(ago / 60)}m ago`
    if (ago < 86400) return `${Math.floor(ago / 3600)}h ago`
    return `${Math.floor(ago / 86400)}d ago`
  } catch { return '—' }
}

function wrColor(wr: number | null, hi = 60, mid = 45): string {
  if (wr == null) return 'var(--dim)'
  return wr >= hi ? '#00d48a' : wr >= mid ? '#f59e0b' : '#ef4444'
}

// ── WeightBar ────────────────────────────────────────────────────────────────

function WeightBar({ current, target }: { current: number; target: number }) {
  const fill = target > 0 ? Math.min(1, current / target) : 0
  const over = current > target
  return (
    <div style={{
      width: 52, height: 3,
      background: 'rgba(255,255,255,0.06)',
      borderRadius: 2, overflow: 'hidden', flexShrink: 0,
    }}>
      <div style={{
        height: '100%',
        width: `${Math.round(fill * 100)}%`,
        background: over ? '#f59e0b' : current > 0 ? '#00d48a' : 'transparent',
        borderRadius: 2,
        transition: 'width 0.5s ease',
      }} />
    </div>
  )
}

// ── TrendBadge — Patch 130 ────────────────────────────────────────────────────

function TrendBadge({ trend, held }: { trend?: string; held: boolean }) {
  if (!trend || trend === 'NEUTRAL') {
    return <span style={{ color: '#2d4060', fontSize: 9, ...MONO }}>—</span>
  }
  if (trend === 'UPTREND') {
    return <span style={{ color: '#00d48a', fontSize: 9, fontWeight: 700, ...MONO }}>▲</span>
  }
  // DOWNTREND
  return (
    <span style={{ display: 'inline-flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
      <span style={{ color: '#ef4444', fontSize: 9, fontWeight: 700, ...MONO }}>▼</span>
      {held && (
        <span style={{
          fontSize: 6, fontWeight: 700, letterSpacing: '0.06em',
          padding: '1px 4px', borderRadius: 2,
          background: 'rgba(245,158,11,0.08)',
          border: '1px solid rgba(245,158,11,0.28)',
          color: '#f59e0b', whiteSpace: 'nowrap', ...MONO,
        }}>SELL?</span>
      )}
    </span>
  )
}

// ── SignalBadge — Patch 134 ───────────────────────────────────────────────────

function SignalBadge({ signalType }: { signalType?: string }) {
  if (!signalType || signalType === 'HOLD') return null
  if (signalType === 'DCA_NOW') {
    return (
      <span style={{
        fontSize: 6, fontWeight: 700, letterSpacing: '0.06em',
        padding: '1px 4px', borderRadius: 2,
        background: 'rgba(0,212,138,0.10)',
        border: '1px solid rgba(0,212,138,0.30)',
        color: '#00d48a', whiteSpace: 'nowrap', ...MONO,
      }}>DCA</span>
    )
  }
  if (signalType === 'WATCH') {
    return (
      <span style={{
        fontSize: 6, fontWeight: 700, letterSpacing: '0.06em',
        padding: '1px 4px', borderRadius: 2,
        background: 'rgba(245,158,11,0.08)',
        border: '1px solid rgba(245,158,11,0.25)',
        color: '#f59e0b', whiteSpace: 'nowrap', ...MONO,
      }}>WATCH</span>
    )
  }
  if (signalType === 'AVOID') {
    return (
      <span style={{
        fontSize: 6, fontWeight: 700, letterSpacing: '0.06em',
        padding: '1px 4px', borderRadius: 2,
        background: 'rgba(239,68,68,0.06)',
        border: '1px solid rgba(239,68,68,0.18)',
        color: 'rgba(239,68,68,0.55)', whiteSpace: 'nowrap', ...MONO,
      }}>AVOID</span>
    )
  }
  return null
}

// ── Shared style constants ────────────────────────────────────────────────────

const CARD: React.CSSProperties = {
  background: 'rgba(255,255,255,0.02)',
  border: '1px solid rgba(255,255,255,0.05)',
  borderRadius: 6,
  padding: '12px 14px',
}

// ── HoldingsTable ─────────────────────────────────────────────────────────────

interface HoldingsTableProps {
  holdings:    SpotHolding[]
  isLoading:   boolean
  learningData: SpotSignalsResponse['learning'] | undefined
  heldCount:   number
  buyBusy:     Set<string>
  sellBusy:    Set<string>
  buyAmts:     Record<string, string>
  setBuyAmts:  React.Dispatch<React.SetStateAction<Record<string, string>>>
  signalData:  Record<string, SpotSignal>
  doBuy:       (h: SpotHolding) => void
  doSell:      (h: SpotHolding) => void
}

function HoldingsTable({
  holdings, isLoading, learningData, heldCount,
  buyBusy, sellBusy, buyAmts, setBuyAmts,
  signalData, doBuy, doSell,
}: HoldingsTableProps) {
  return (
    <div style={{
      background: 'rgba(255,255,255,0.018)',
      border: '1px solid rgba(255,255,255,0.06)',
      borderRadius: 8, overflow: 'hidden',
    }}>
      {/* Table header */}
      <div style={{
        padding: '14px 16px 0',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <span style={{ color: '#3d5a78', fontSize: 8, letterSpacing: '0.12em', ...MONO, fontWeight: 700 }}>
          HOLDINGS
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {/* Patch 134: learning loop progress bar */}
          {learningData && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ color: '#2d4060', fontSize: 7, letterSpacing: '0.08em', ...MONO }}>
                LEARNING
              </span>
              <div style={{
                width: 56, height: 3,
                background: 'rgba(255,255,255,0.05)',
                borderRadius: 2, overflow: 'hidden',
              }}>
                <div style={{
                  height: '100%',
                  width: `${learningData.complete_pct}%`,
                  background: learningData.confidence !== 'pending' ? '#00d48a' : '#3d5a78',
                  borderRadius: 2,
                  transition: 'width 0.6s ease',
                }} />
              </div>
              <span style={{
                color: learningData.confidence !== 'pending' ? '#00d48a' : '#3d5a78',
                fontSize: 7, ...MONO,
              }}>
                {learningData.complete} / {learningData.tuner_threshold}
                {learningData.confidence !== 'pending'
                  ? ` · ${learningData.confidence.toUpperCase()} CONF`
                  : ' · tuner pending'}
              </span>
            </div>
          )}
          <span style={{ color: '#2d4060', fontSize: 8, ...MONO }}>
            {heldCount} / {holdings.length} positions
          </span>
        </div>
      </div>

      {isLoading ? (
        <div style={{ padding: '20px 16px', color: '#2d4060', fontSize: 10, ...MONO }}>loading…</div>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', ...MONO, marginTop: 10 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
              {[
                { label: 'TOKEN',          align: 'left'   },
                { label: 'WEIGHT → TARGET', align: 'left'  },
                { label: 'PRICE',          align: 'right'  },
                { label: 'HELD',           align: 'right'  },
                { label: 'AVG COST',       align: 'right'  },
                { label: 'PnL',            align: 'right'  },
                { label: 'BUY',            align: 'center' },
                { label: '',               align: 'center' },
              ].map(col => (
                <th key={col.label} style={{
                  padding: '0 12px 10px',
                  textAlign: col.align as 'left' | 'right' | 'center',
                  color: '#1e2d3d', fontSize: 7, letterSpacing: '0.1em', fontWeight: 700,
                }}>
                  {col.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {holdings.map(h => {
              const held    = h.token_amount > 0
              const buying  = buyBusy.has(h.symbol)
              const selling = sellBusy.has(h.symbol)
              const buyAmt  = buyAmts[h.symbol] ?? ''

              return (
                <tr
                  key={h.symbol}
                  style={{
                    borderTop: '1px solid rgba(255,255,255,0.03)',
                    background: held ? 'rgba(0,212,138,0.015)' : 'transparent',
                  }}
                >

                  {/* TOKEN + trend badge (Patch 130) + signal badge (Patch 134) */}
                  <td style={{ padding: '11px 12px' }}>
                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6 }}>
                      <div>
                        <div style={{ color: held ? '#c0cfe0' : '#4d5a6e', fontWeight: 700, fontSize: 12, letterSpacing: '0.04em' }}>
                          {h.symbol}
                        </div>
                        <div style={{ color: '#1e2d3d', fontSize: 8, marginTop: 2 }}>{h.name}</div>
                      </div>
                      <TrendBadge trend={h.trend} held={held} />
                      <SignalBadge signalType={signalData[h.symbol]?.signal_type} />
                    </div>
                  </td>

                  {/* WEIGHT → TARGET */}
                  <td style={{ padding: '11px 12px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                      <WeightBar current={h.current_pct} target={h.target_pct} />
                      <span style={{ color: held ? '#7c9fd4' : '#2d4060', fontSize: 9, minWidth: 32 }}>
                        {held ? `${h.current_pct.toFixed(1)}%` : '0%'}
                      </span>
                      <span style={{ color: '#1e2d3d', fontSize: 8 }}>→</span>
                      <span style={{ color: '#3d5a78', fontSize: 9 }}>{h.target_pct}%</span>
                    </div>
                  </td>

                  {/* PRICE + 24h change (Patch 130) */}
                  <td style={{ padding: '11px 12px', textAlign: 'right' }}>
                    <div style={{ color: '#8a9ab0', fontSize: 10 }}>
                      {h.current_price > 0 ? fPrice(h.current_price) : '—'}
                    </div>
                    {h.price_change_24h != null && (
                      <div style={{
                        color: h.price_change_24h > 0 ? '#00d48a' : h.price_change_24h < 0 ? '#ef4444' : '#4d5a6e',
                        fontSize: 8, marginTop: 2, ...MONO,
                      }}>
                        {h.price_change_24h > 0 ? '+' : ''}{h.price_change_24h.toFixed(1)}%
                      </div>
                    )}
                  </td>

                  {/* HELD (value + token amount) */}
                  <td style={{ padding: '11px 12px', textAlign: 'right' }}>
                    {held ? (
                      <>
                        <div style={{ color: '#c0cfe0', fontSize: 10, fontWeight: 600 }}>
                          {fUsd(h.current_value)}
                        </div>
                        <div style={{ color: '#3d5a78', fontSize: 8, marginTop: 2 }}>
                          {h.token_amount.toFixed(4)}
                        </div>
                      </>
                    ) : (
                      <span style={{ color: '#1e2d3d', fontSize: 10 }}>—</span>
                    )}
                  </td>

                  {/* AVG COST */}
                  <td style={{ padding: '11px 12px', textAlign: 'right', color: held ? '#4d5a6e' : '#1e2d3d', fontSize: 10 }}>
                    {held ? fPrice(h.avg_cost_usd) : '—'}
                  </td>

                  {/* PnL */}
                  <td style={{ padding: '11px 12px', textAlign: 'right' }}>
                    {held ? (
                      <>
                        <div style={{ color: pnlCol(h.pnl_pct), fontSize: 10, fontWeight: 700 }}>
                          {fPct(h.pnl_pct)}
                        </div>
                        <div style={{ color: pnlCol(h.pnl_usd), fontSize: 8, marginTop: 2 }}>
                          {fUsd(h.pnl_usd, true)}
                        </div>
                      </>
                    ) : (
                      <span style={{ color: '#1e2d3d', fontSize: 10 }}>—</span>
                    )}
                  </td>

                  {/* BUY input + button (joined) */}
                  <td style={{ padding: '11px 8px' }}>
                    <div style={{ display: 'flex' }}>
                      <span style={{
                        padding: '4px 6px',
                        background: 'rgba(255,255,255,0.03)',
                        border: '1px solid rgba(255,255,255,0.07)',
                        borderRight: 'none',
                        borderRadius: '4px 0 0 4px',
                        color: '#2d4060', fontSize: 9, ...MONO,
                        display: 'flex', alignItems: 'center',
                      }}>$</span>
                      <input
                        type="number"
                        placeholder="—"
                        value={buyAmt}
                        onChange={e => setBuyAmts(p => ({ ...p, [h.symbol]: e.target.value }))}
                        onKeyDown={e => e.key === 'Enter' && doBuy(h)}
                        style={{
                          width: 55, padding: '4px 6px',
                          background: 'rgba(255,255,255,0.03)',
                          border: '1px solid rgba(255,255,255,0.07)',
                          borderLeft: 'none', borderRight: 'none',
                          color: '#c0cfe0',
                          fontFamily: 'JetBrains Mono, monospace', fontSize: 10,
                          outline: 'none',
                        }}
                      />
                      <button
                        onClick={() => doBuy(h)}
                        disabled={buying}
                        style={{
                          padding: '4px 9px',
                          background: buying ? 'transparent' : 'rgba(0,212,138,0.07)',
                          border: `1px solid ${buying ? 'rgba(255,255,255,0.06)' : 'rgba(0,212,138,0.22)'}`,
                          borderRadius: '0 4px 4px 0',
                          color: buying ? '#2d4060' : '#00d48a',
                          cursor: buying ? 'default' : 'pointer',
                          fontFamily: 'JetBrains Mono, monospace',
                          fontSize: 8, fontWeight: 700, letterSpacing: '0.08em',
                        }}
                      >
                        {buying ? '…' : 'BUY'}
                      </button>
                    </div>
                  </td>

                  {/* SELL button */}
                  <td style={{ padding: '11px 12px', textAlign: 'center' }}>
                    {held && (
                      <button
                        onClick={() => doSell(h)}
                        disabled={selling}
                        style={{
                          padding: '4px 9px',
                          background: 'transparent',
                          border: '1px solid rgba(239,68,68,0.18)',
                          borderRadius: 4,
                          color: selling ? '#2d4060' : 'rgba(239,68,68,0.55)',
                          cursor: selling ? 'default' : 'pointer',
                          fontFamily: 'JetBrains Mono, monospace',
                          fontSize: 7, fontWeight: 700, letterSpacing: '0.06em',
                        }}
                      >
                        {selling ? '…' : 'SELL'}
                      </button>
                    )}
                  </td>

                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}

// ── AnalyticsPanel ────────────────────────────────────────────────────────────

interface AnalyticsPanelProps {
  signalData:   Record<string, SpotSignal>
  an:           SpotAnalytics | undefined
  learningData: SpotSignalsResponse['learning'] | undefined
}

function AnalyticsPanel({ signalData, an, learningData }: AnalyticsPanelProps) {
  return (
    <div style={{
      background: 'rgba(255,255,255,0.018)',
      border: '1px solid rgba(255,255,255,0.06)',
      borderRadius: 8, overflow: 'hidden',
    }}>

      {/* Section header */}
      <div style={{
        padding: '14px 16px 0',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <div>
          <span style={{ color: '#3d5a78', fontSize: 8, letterSpacing: '0.12em', ...MONO, fontWeight: 700 }}>
            LEARNING SYSTEM
          </span>
          <div style={{ color: '#1e2d3d', fontSize: 7, marginTop: 3, ...MONO }}>
            DCA TIMING SIGNALS · 7-DAY OUTCOME TRACKING · AUTO-TUNER
          </div>
        </div>
        {/* Learning progress bar */}
        {learningData && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
            <div style={{ width: 72, height: 3, background: 'rgba(255,255,255,0.05)', borderRadius: 2, overflow: 'hidden' }}>
              <div style={{
                height: '100%',
                width: `${learningData.complete_pct}%`,
                background: learningData.confidence !== 'pending' ? '#00d48a' : '#3d5a78',
                borderRadius: 2, transition: 'width 0.6s ease',
              }} />
            </div>
            <span style={{
              color: learningData.confidence !== 'pending' ? '#00d48a' : '#3d5a78',
              fontSize: 8, ...MONO,
            }}>
              {learningData.complete} / {learningData.tuner_threshold} outcomes
              {learningData.confidence !== 'pending'
                ? ` · ${learningData.confidence.toUpperCase()} CONF`
                : ' · tuner pending'}
            </span>
          </div>
        )}
      </div>

      <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 14 }}>

        {/* ── Current Signals Table ── */}
        {Object.keys(signalData).length > 0 && (
          <div>
            <div style={{ color: '#2d4060', fontSize: 7, letterSpacing: '0.1em', ...MONO, marginBottom: 8, fontWeight: 700 }}>
              CURRENT SIGNALS
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', ...MONO }}>
              <thead>
                <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                  {['TOKEN', 'SCORE', 'SIGNAL', 'TREND', '24H', '6H', 'WEIGHT GAP', 'F&G'].map(h => (
                    <th key={h} style={{
                      padding: '0 10px 7px',
                      textAlign: h === 'TOKEN' ? 'left' : 'right',
                      color: '#1e2d3d', fontSize: 7, letterSpacing: '0.1em', fontWeight: 700,
                    }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {Object.entries(signalData)
                  .sort(([, a], [, b]) => b.score - a.score)
                  .map(([sym, sv]) => {
                    const stColor = sv.signal_type === 'DCA_NOW' ? '#00d48a'
                      : sv.signal_type === 'WATCH' ? '#f59e0b'
                      : sv.signal_type === 'AVOID' ? 'rgba(239,68,68,0.55)'
                      : '#2d4060'
                    const tColor = sv.trend === 'UPTREND' ? '#00d48a'
                      : sv.trend === 'DOWNTREND' ? '#ef4444'
                      : '#2d4060'
                    const tArrow = sv.trend === 'UPTREND' ? '▲' : sv.trend === 'DOWNTREND' ? '▼' : '—'
                    return (
                      <tr key={sym} style={{ borderTop: '1px solid rgba(255,255,255,0.025)' }}>
                        <td style={{ padding: '7px 10px', color: '#c0cfe0', fontWeight: 700, fontSize: 11 }}>{sym}</td>
                        <td style={{ padding: '7px 10px', textAlign: 'right', color: '#8a9ab0', fontSize: 10 }}>{sv.score.toFixed(1)}</td>
                        <td style={{ padding: '7px 10px', textAlign: 'right' }}>
                          <span style={{ color: stColor, fontSize: 8, fontWeight: 700 }}>{sv.signal_type}</span>
                        </td>
                        <td style={{ padding: '7px 10px', textAlign: 'right', color: tColor, fontSize: 9 }}>{tArrow}</td>
                        <td style={{
                          padding: '7px 10px', textAlign: 'right', fontSize: 9,
                          color: sv.h24 == null ? '#1e2d3d' : sv.h24 > 0 ? '#00d48a' : '#ef4444',
                        }}>
                          {sv.h24 != null ? `${sv.h24 >= 0 ? '+' : ''}${sv.h24.toFixed(1)}%` : '—'}
                        </td>
                        <td style={{
                          padding: '7px 10px', textAlign: 'right', fontSize: 9,
                          color: sv.h6 == null ? '#1e2d3d' : sv.h6 > 0 ? '#00d48a' : '#ef4444',
                        }}>
                          {sv.h6 != null ? `${sv.h6 >= 0 ? '+' : ''}${sv.h6.toFixed(1)}%` : '—'}
                        </td>
                        <td style={{
                          padding: '7px 10px', textAlign: 'right', fontSize: 9,
                          color: sv.gap > 5 ? '#7c9fd4' : sv.gap < -5 ? '#f59e0b' : '#3d5a78',
                        }}>
                          {sv.gap >= 0 ? '+' : ''}{sv.gap.toFixed(1)}%
                        </td>
                        <td style={{ padding: '7px 10px', textAlign: 'right', fontSize: 9, color: '#4d5a6e' }}>
                          {sv.fg ?? '—'}
                        </td>
                      </tr>
                    )
                  })}
              </tbody>
            </table>
          </div>
        )}

        {/* ── Analytics Breakdowns (appear once 7d outcomes exist) ── */}
        {(!an || an.complete === 0) ? (
          <div style={{
            padding: '14px 16px',
            background: 'rgba(255,255,255,0.01)',
            border: '1px solid rgba(255,255,255,0.04)',
            borderRadius: 6, textAlign: 'center',
          }}>
            <div style={{ color: '#3d5a78', fontSize: 9, ...MONO }}>collecting data</div>
            <div style={{ color: '#1e2d3d', fontSize: 8, marginTop: 5, ...MONO }}>
              7-day outcomes fill automatically · first batch arrives ~7 days after signals are logged · need 20 complete to tune
            </div>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>

            {/* Signal type + F&G + Tuner row */}
            <div style={{ display: 'flex', gap: 8 }}>

              {/* Signal Type Performance */}
              <div style={{ ...CARD, flex: 1 }}>
                <div style={{ color: '#2d4060', fontSize: 7, letterSpacing: '0.1em', ...MONO, marginBottom: 10, fontWeight: 700 }}>
                  SIGNAL TYPE
                </div>
                {an.signal_breakdown.map(sb => {
                  const wrCol = wrColor(sb.win_rate_7d)
                  return (
                    <div key={sb.label} style={{ marginBottom: 10 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 5 }}>
                        <span style={{
                          color: sb.label === 'DCA_NOW' ? '#00d48a' : '#f59e0b',
                          fontSize: 9, fontWeight: 700, ...MONO,
                        }}>{sb.label}</span>
                        <span style={{ color: '#2d4060', fontSize: 8, ...MONO }}>{sb.count} outcomes</span>
                      </div>
                      <div style={{ display: 'flex', gap: 12 }}>
                        <div>
                          <div style={{ color: '#1e2d3d', fontSize: 7, ...MONO }}>WIN RATE 7D</div>
                          <div style={{ color: wrCol, fontSize: 14, fontWeight: 800, ...MONO, marginTop: 2 }}>
                            {sb.win_rate_7d != null ? `${sb.win_rate_7d.toFixed(0)}%` : '—'}
                          </div>
                        </div>
                        <div>
                          <div style={{ color: '#1e2d3d', fontSize: 7, ...MONO }}>AVG 7D</div>
                          <div style={{
                            fontSize: 13, fontWeight: 700, ...MONO, marginTop: 2,
                            color: sb.avg_return_7d == null ? '#1e2d3d'
                              : sb.avg_return_7d >= 0 ? '#00d48a' : '#ef4444',
                          }}>
                            {sb.avg_return_7d != null
                              ? `${sb.avg_return_7d >= 0 ? '+' : ''}${sb.avg_return_7d.toFixed(1)}%`
                              : '—'}
                          </div>
                        </div>
                      </div>
                      {sb.win_rate_7d != null && (
                        <div style={{ height: 2, background: 'rgba(255,255,255,0.04)', borderRadius: 1, marginTop: 7, overflow: 'hidden' }}>
                          <div style={{ height: '100%', width: `${sb.win_rate_7d}%`, background: wrCol, borderRadius: 1 }} />
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>

              {/* F&G Bucket Performance */}
              <div style={{ ...CARD, flex: 1 }}>
                <div style={{ color: '#2d4060', fontSize: 7, letterSpacing: '0.1em', ...MONO, marginBottom: 10, fontWeight: 700 }}>
                  FEAR &amp; GREED TIMING
                </div>
                {an.fg_breakdown.map(fb => {
                  const wrCol = wrColor(fb.win_rate_7d)
                  return (
                    <div key={fb.label} style={{
                      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                      padding: '5px 0', borderBottom: '1px solid rgba(255,255,255,0.025)',
                    }}>
                      <span style={{ color: '#4d5a6e', fontSize: 8, ...MONO }}>{fb.label}</span>
                      <div style={{ display: 'flex', gap: 14, alignItems: 'center' }}>
                        <span style={{ color: '#2d4060', fontSize: 7, ...MONO }}>{fb.count}×</span>
                        <span style={{ color: wrCol, fontSize: 9, fontWeight: 700, ...MONO, minWidth: 28, textAlign: 'right' }}>
                          {fb.win_rate_7d != null ? `${fb.win_rate_7d.toFixed(0)}%` : '—'}
                        </span>
                        <span style={{
                          fontSize: 9, ...MONO, minWidth: 38, textAlign: 'right',
                          color: fb.avg_return_7d == null ? '#1e2d3d'
                            : fb.avg_return_7d >= 0 ? '#00d48a' : '#ef4444',
                        }}>
                          {fb.avg_return_7d != null
                            ? `${fb.avg_return_7d >= 0 ? '+' : ''}${fb.avg_return_7d.toFixed(1)}%`
                            : '—'}
                        </span>
                      </div>
                    </div>
                  )
                })}
              </div>

              {/* Tuner Output */}
              <div style={{
                flex: 1,
                background: 'rgba(255,255,255,0.02)',
                border: `1px solid ${an.tuner ? (an.tuner.confidence === 'high' ? 'rgba(0,212,138,0.12)' : an.tuner.confidence === 'medium' ? 'rgba(245,158,11,0.12)' : 'rgba(255,255,255,0.05)') : 'rgba(255,255,255,0.05)'}`,
                borderRadius: 6, padding: '12px 14px',
              }}>
                <div style={{ color: '#2d4060', fontSize: 7, letterSpacing: '0.1em', ...MONO, marginBottom: 10, fontWeight: 700 }}>
                  AUTO-TUNER
                </div>
                {an.tuner ? (
                  <>
                    <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 10 }}>
                      <span style={{
                        fontSize: 7, fontWeight: 700, letterSpacing: '0.06em',
                        padding: '2px 7px', borderRadius: 3,
                        background: an.tuner.confidence === 'high' ? 'rgba(0,212,138,0.08)' : an.tuner.confidence === 'medium' ? 'rgba(245,158,11,0.08)' : 'rgba(77,90,110,0.1)',
                        border: `1px solid ${an.tuner.confidence === 'high' ? 'rgba(0,212,138,0.25)' : an.tuner.confidence === 'medium' ? 'rgba(245,158,11,0.25)' : 'rgba(77,90,110,0.2)'}`,
                        color: an.tuner.confidence === 'high' ? '#00d48a' : an.tuner.confidence === 'medium' ? '#f59e0b' : '#4d5a6e',
                        ...MONO,
                      }}>
                        {an.tuner.confidence.toUpperCase()} CONF
                      </span>
                      <span style={{ color: '#1e2d3d', fontSize: 7, ...MONO }}>{an.tuner.sample_size} samples</span>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 12px' }}>
                      <div>
                        <div style={{ color: '#1e2d3d', fontSize: 7, ...MONO }}>MIN SCORE</div>
                        <div style={{ color: '#c0cfe0', fontSize: 16, fontWeight: 800, ...MONO, marginTop: 2 }}>
                          {an.tuner.min_score.toFixed(0)}
                        </div>
                      </div>
                      <div>
                        <div style={{ color: '#1e2d3d', fontSize: 7, ...MONO }}>WIN RATE 7D</div>
                        <div style={{
                          fontSize: 16, fontWeight: 800, ...MONO, marginTop: 2,
                          color: wrColor(an.tuner.win_rate),
                        }}>
                          {an.tuner.win_rate.toFixed(0)}%
                        </div>
                      </div>
                    </div>
                  </>
                ) : (
                  <div>
                    <div style={{ color: '#3d5a78', fontSize: 9, ...MONO }}>no data yet</div>
                    <div style={{ color: '#1e2d3d', fontSize: 8, marginTop: 5, ...MONO }}>
                      tuner fires at 20 complete 7d outcomes
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Per-Token Performance */}
            <div style={CARD}>
              <div style={{ color: '#2d4060', fontSize: 7, letterSpacing: '0.1em', ...MONO, marginBottom: 10, fontWeight: 700 }}>
                PER-TOKEN PERFORMANCE (7D OUTCOMES)
              </div>
              <div style={{ display: 'flex', gap: 6 }}>
                {an.token_breakdown.map(tb => {
                  const wrCol = wrColor(tb.win_rate_7d)
                  return (
                    <div key={tb.symbol} style={{
                      flex: 1, textAlign: 'center',
                      background: 'rgba(255,255,255,0.015)',
                      border: '1px solid rgba(255,255,255,0.04)',
                      borderRadius: 5, padding: '8px 6px',
                    }}>
                      <div style={{ color: '#8a9ab0', fontSize: 10, fontWeight: 700, ...MONO }}>{tb.symbol}</div>
                      <div style={{ color: wrCol, fontSize: 13, fontWeight: 800, ...MONO, marginTop: 5 }}>
                        {tb.win_rate_7d != null ? `${tb.win_rate_7d.toFixed(0)}%` : '—'}
                      </div>
                      <div style={{
                        fontSize: 8, ...MONO, marginTop: 3,
                        color: tb.avg_return_7d == null ? '#1e2d3d'
                          : tb.avg_return_7d >= 0 ? '#00d48a' : '#ef4444',
                      }}>
                        {tb.avg_return_7d != null
                          ? `${tb.avg_return_7d >= 0 ? '+' : ''}${tb.avg_return_7d.toFixed(1)}%`
                          : '—'}
                      </div>
                      <div style={{ color: '#1e2d3d', fontSize: 7, marginTop: 3, ...MONO }}>{tb.count}×</div>
                    </div>
                  )
                })}
              </div>
            </div>

          </div>
        )}

        {/* ── Signal History ── */}
        {an && an.recent_signals.length > 0 && (
          <div>
            <div style={{ color: '#2d4060', fontSize: 7, letterSpacing: '0.1em', ...MONO, marginBottom: 8, fontWeight: 700 }}>
              SIGNAL HISTORY
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', ...MONO }}>
              <thead>
                <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                  {['TOKEN', 'TYPE', 'SCORE', '24H', 'F&G', 'TREND', 'GAP', '7D RETURN', 'STATUS'].map(h => (
                    <th key={h} style={{
                      padding: '0 9px 7px',
                      textAlign: h === 'TOKEN' || h === 'TYPE' ? 'left' : 'right',
                      color: '#1e2d3d', fontSize: 7, letterSpacing: '0.09em', fontWeight: 700,
                    }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {an.recent_signals.map(sig => {
                  const stColor = sig.signal_type === 'DCA_NOW' ? '#00d48a'
                    : sig.signal_type === 'WATCH' ? '#f59e0b'
                    : '#2d4060'
                  const retColor = sig.return_7d_pct == null ? '#1e2d3d'
                    : sig.return_7d_pct > 0 ? '#00d48a' : '#ef4444'
                  const tArrow = sig.trend_at_signal === 'UPTREND' ? '▲'
                    : sig.trend_at_signal === 'DOWNTREND' ? '▼' : '—'
                  const tColor = sig.trend_at_signal === 'UPTREND' ? '#00d48a'
                    : sig.trend_at_signal === 'DOWNTREND' ? '#ef4444' : '#2d4060'
                  return (
                    <tr key={sig.id} style={{ borderTop: '1px solid rgba(255,255,255,0.025)' }}>
                      <td style={{ padding: '6px 9px', color: '#c0cfe0', fontWeight: 700, fontSize: 10 }}>{sig.symbol}</td>
                      <td style={{ padding: '6px 9px' }}>
                        <span style={{ color: stColor, fontSize: 7, fontWeight: 700 }}>{sig.signal_type}</span>
                      </td>
                      <td style={{ padding: '6px 9px', textAlign: 'right', color: '#4d5a6e', fontSize: 9 }}>
                        {sig.score.toFixed(1)}
                      </td>
                      <td style={{
                        padding: '6px 9px', textAlign: 'right', fontSize: 9,
                        color: sig.h24_at_signal == null ? '#1e2d3d'
                          : sig.h24_at_signal > 0 ? '#00d48a' : '#ef4444',
                      }}>
                        {sig.h24_at_signal != null
                          ? `${sig.h24_at_signal >= 0 ? '+' : ''}${sig.h24_at_signal.toFixed(1)}%`
                          : '—'}
                      </td>
                      <td style={{ padding: '6px 9px', textAlign: 'right', color: '#4d5a6e', fontSize: 9 }}>
                        {sig.fg_at_signal ?? '—'}
                      </td>
                      <td style={{ padding: '6px 9px', textAlign: 'right', color: tColor, fontSize: 9 }}>{tArrow}</td>
                      <td style={{
                        padding: '6px 9px', textAlign: 'right', fontSize: 9,
                        color: sig.portfolio_gap == null ? '#1e2d3d'
                          : sig.portfolio_gap > 0 ? '#7c9fd4' : '#f59e0b',
                      }}>
                        {sig.portfolio_gap != null ? `${sig.portfolio_gap >= 0 ? '+' : ''}${sig.portfolio_gap.toFixed(1)}%` : '—'}
                      </td>
                      <td style={{ padding: '6px 9px', textAlign: 'right', color: retColor, fontSize: 9, fontWeight: sig.return_7d_pct != null ? 700 : 400 }}>
                        {sig.return_7d_pct != null
                          ? `${sig.return_7d_pct >= 0 ? '+' : ''}${sig.return_7d_pct.toFixed(1)}%`
                          : '—'}
                      </td>
                      <td style={{ padding: '6px 9px', textAlign: 'right' }}>
                        <span style={{
                          fontSize: 7, fontWeight: 700,
                          color: sig.status === 'COMPLETE' ? '#00d48a' : '#3d5a78',
                        }}>
                          {sig.status === 'COMPLETE' ? '✓ DONE' : '⏳ 7D'}
                        </span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

      </div>
    </div>
  )
}

// ── SpotPage ─────────────────────────────────────────────────────────────────

export function SpotPage() {
  const qc = useQueryClient()

  const [budget, setBudget]         = useState('200')
  const [advice, setAdvice]         = useState<AdviceItem[] | null>(null)
  const [advLoading, setAdvLoading] = useState(false)
  const [buyAmts, setBuyAmts]       = useState<Record<string, string>>({})
  const [buyBusy, setBuyBusy]       = useState<Set<string>>(new Set())
  const [sellBusy, setSellBusy]     = useState<Set<string>>(new Set())
  const [flash, setFlash]           = useState<{ msg: string; ok: boolean } | null>(null)

  // ── Data queries ──────────────────────────────────────────────────────────

  const spotQ = useQuery<SpotStatus>({
    queryKey:        ['spot-status'],
    queryFn:         async () => (await api.get('/spot/status')).data,
    refetchInterval: 30_000,
  })

  const histQ = useQuery<SpotTx[]>({
    queryKey:        ['spot-history'],
    queryFn:         async () => (await api.get('/spot/history')).data?.transactions ?? [],
    refetchInterval: 60_000,
  })

  // Patch 134 — DCA signal scores + learning progress
  const signalsQ = useQuery<SpotSignalsResponse>({
    queryKey:        ['spot-signals'],
    queryFn:         async () => (await api.get('/spot/signals')).data,
    refetchInterval: 60_000,
  })

  // Patch 134 — Analytics (signal performance breakdowns + history)
  const analyticsQ = useQuery<SpotAnalytics>({
    queryKey:        ['spot-analytics'],
    queryFn:         async () => (await api.get('/spot/analytics')).data,
    refetchInterval: 120_000,
  })

  const data     = spotQ.data
  const holdings = data?.holdings ?? []
  const dryRun   = data?.dry_run ?? true
  const invested = data?.total_invested ?? 0
  const value    = data?.total_value    ?? 0
  const pnlUsd   = data?.total_pnl_usd  ?? 0
  const pnlPct   = data?.total_pnl_pct  ?? 0
  const heldCount = holdings.filter(h => h.token_amount > 0).length

  // Patch 134 — signal + analytics data helpers
  const signalData   = signalsQ.data?.signals ?? {}
  const learningData = signalsQ.data?.learning
  const an           = analyticsQ.data

  // ── Action helpers ────────────────────────────────────────────────────────

  function notify(msg: string, ok = true) {
    setFlash({ msg, ok })
    setTimeout(() => setFlash(null), 5000)
  }

  function invalidate() {
    qc.invalidateQueries({ queryKey: ['spot-status'] })
    qc.invalidateQueries({ queryKey: ['spot-history'] })
  }

  async function getAdvice() {
    const amt = parseFloat(budget)
    if (!amt || amt < 5) { notify('Budget must be at least $5', false); return }
    setAdvLoading(true)
    try {
      const r = await api.get(`/spot/advice?amount=${amt}`)
      setAdvice(r.data?.advice ?? [])
    } catch (e: any) {
      notify(e?.response?.data?.detail ?? e.message, false)
    }
    setAdvLoading(false)
  }

  function applyChip(item: AdviceItem) {
    setBuyAmts(prev => ({ ...prev, [item.symbol]: item.suggested_usd.toFixed(2) }))
  }

  function applyAll() {
    if (!advice) return
    const updates: Record<string, string> = {}
    advice.forEach(a => { updates[a.symbol] = a.suggested_usd.toFixed(2) })
    setBuyAmts(prev => ({ ...prev, ...updates }))
  }

  async function doBuy(h: SpotHolding) {
    const amt = parseFloat(buyAmts[h.symbol] ?? '')
    if (!amt || amt < 1) { notify(`Set a buy amount for ${h.symbol}`, false); return }
    setBuyBusy(s => new Set(s).add(h.symbol))
    try {
      const r = await api.post('/spot/buy', { symbol: h.symbol, mint: h.mint, amount_usd: amt })
      if (r.data?.success) {
        notify(
          `${dryRun ? '[PAPER] ' : ''}${h.symbol} — ` +
          `${r.data.token_amount.toFixed(4)} tokens @ ${fPrice(r.data.price_usd)}`
        )
        setBuyAmts(prev => { const n = { ...prev }; delete n[h.symbol]; return n })
        invalidate()
      } else {
        notify(r.data?.error ?? 'Buy failed', false)
      }
    } catch (e: any) {
      notify(e?.response?.data?.detail ?? e.message, false)
    }
    setBuyBusy(s => { const n = new Set(s); n.delete(h.symbol); return n })
  }

  async function doSell(h: SpotHolding) {
    if (!confirm(`Sell all ${h.symbol}?\n${h.token_amount.toFixed(4)} tokens · ~${fUsd(h.current_value)}`)) return
    setSellBusy(s => new Set(s).add(h.symbol))
    try {
      const r = await api.post(`/spot/sell/${h.symbol}`)
      if (r.data?.success) {
        notify(`${dryRun ? '[PAPER] ' : ''}${h.symbol} sold`)
        invalidate()
      } else {
        notify(r.data?.error ?? 'Sell failed', false)
      }
    } catch (e: any) {
      notify(e?.response?.data?.detail ?? e.message, false)
    }
    setSellBusy(s => { const n = new Set(s); n.delete(h.symbol); return n })
  }

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div style={{
      maxWidth: 1200, margin: '0 auto',
      padding: '20px 20px 48px',
      display: 'flex', flexDirection: 'column', gap: 12,
    }}>

      {/* ── Header ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <span style={{
          color: '#f59e0b', fontFamily: 'JetBrains Mono, monospace',
          fontWeight: 700, fontSize: 13, letterSpacing: '0.14em',
        }}>
          SPOT ACCUM
        </span>
        <span className="badge" style={{
          color: dryRun ? 'var(--amber)' : 'var(--green)',
          background: dryRun ? 'rgba(245,158,11,0.1)' : 'rgba(0,212,138,0.1)',
          border: `1px solid ${dryRun ? 'rgba(245,158,11,0.25)' : 'rgba(0,212,138,0.25)'}`,
          fontSize: 9,
        }}>
          {dryRun ? 'PAPER' : 'LIVE'}
        </span>
        <span style={{ color: 'var(--dim)', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>
          WIF · JUP · BONK · RAY · POPCAT · PYTH · ORCA
        </span>
        {/* Right stats */}
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ color: 'var(--dim)', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}>basket</span>
            <span style={{ color: 'var(--text2)', fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, fontSize: 12 }}>
              {heldCount} / {holdings.length}
            </span>
          </div>
          {invested > 0 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ color: 'var(--dim)', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}>invested</span>
              <span style={{ color: 'var(--text2)', fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, fontSize: 12 }}>
                ${invested.toFixed(0)}
              </span>
            </div>
          )}
          {Math.abs(pnlUsd) > 0.01 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ color: 'var(--dim)', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}>PnL</span>
              <span style={{
                color: pnlUsd >= 0 ? 'var(--green)' : 'var(--red)',
                fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, fontSize: 12,
              }}>
                {pnlUsd >= 0 ? '+' : ''}${pnlUsd.toFixed(2)}
              </span>
            </div>
          )}
          {/* Agent pulse */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <span style={{
              width: 5, height: 5, borderRadius: '50%',
              background: '#00d48a', display: 'inline-block',
              boxShadow: '0 0 4px #00d48a88',
            }} />
            <span style={{ color: 'var(--dim)', fontSize: 9, fontFamily: 'JetBrains Mono, monospace' }}>spot_monitor</span>
          </div>
        </div>
      </div>

      {/* ── DCA Signal Engine Status — Patch 134 ── */}
      {learningData && (
        <div style={{
          background: 'rgba(255,255,255,0.02)',
          border: '1px solid rgba(255,255,255,0.06)',
          borderRadius: 8, padding: '12px 16px',
          display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap',
        }}>
          {/* Mode + engine badges */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
            <span style={{
              ...MONO, fontSize: 9, fontWeight: 700, letterSpacing: '0.12em',
              padding: '3px 8px', borderRadius: 3,
              background: dryRun ? 'rgba(245,158,11,0.12)' : 'rgba(0,212,138,0.12)',
              border: `1px solid ${dryRun ? '#f59e0b' : '#00d48a'}44`,
              color: dryRun ? '#f59e0b' : '#00d48a',
            }}>
              {dryRun ? 'PAPER MODE' : 'LIVE MODE'}
            </span>
            <span style={{
              ...MONO, fontSize: 9, fontWeight: 700, letterSpacing: '0.12em',
              padding: '3px 8px', borderRadius: 3,
              background: 'rgba(124,159,212,0.08)',
              border: '1px solid rgba(124,159,212,0.20)',
              color: '#7c9fd4',
            }}>
              DCA SIGNAL ENGINE
            </span>
          </div>

          {/* Learning loop progress bar */}
          <div style={{ flex: 1, minWidth: 200 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 5 }}>
              <span style={{ ...MONO, color: '#2d4060', fontSize: 8, letterSpacing: '0.1em' }}>
                LEARNING LOOP
              </span>
              <span style={{
                ...MONO, fontSize: 8,
                color: learningData.confidence === 'high'   ? '#00d48a'
                     : learningData.confidence === 'medium' ? '#f59e0b'
                     : learningData.confidence === 'low'    ? '#f59e0b'
                     : '#3d5a78',
              }}>
                {learningData.complete} / {learningData.tuner_threshold} outcomes
                {learningData.confidence === 'high'   ? ' · HIGH CONF'
                : learningData.confidence === 'medium' ? ' · MED CONF'
                : learningData.confidence === 'low'    ? ' · LOW CONF'
                :                                        ' · tuner pending'}
              </span>
            </div>
            <div style={{ height: 4, background: 'rgba(255,255,255,0.05)', borderRadius: 2, overflow: 'hidden' }}>
              <div style={{
                height: '100%',
                width: `${learningData.complete_pct}%`,
                background: learningData.confidence !== 'pending' ? '#00d48a' : '#3d5a78',
                borderRadius: 2, transition: 'width 0.6s ease',
              }} />
            </div>
          </div>

          {/* Stat chips — score threshold, active DCA_NOW signals, live F&G */}
          <div style={{ display: 'flex', gap: 14, flexShrink: 0 }}>
            {[
              { lbl: 'SCORE MIN', val: String(learningData.min_score) },
              { lbl: 'DCA NOW',   val: `${Object.values(signalData).filter(s => s.signal_type === 'DCA_NOW').length} / ${holdings.length || Object.values(signalData).length}` },
              { lbl: 'F&G',       val: String(Object.values(signalData)[0]?.fg ?? '—') },
            ].map(({ lbl, val }) => (
              <div key={lbl} style={{ textAlign: 'center' }}>
                <div style={{ ...MONO, color: '#1e2d3d', fontSize: 7, letterSpacing: '0.1em', marginBottom: 2 }}>{lbl}</div>
                <div style={{ ...MONO, color: '#2d4060', fontSize: 11, fontWeight: 700 }}>{val}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Stats row (3 cards) ── */}
      <div style={{ display: 'flex', gap: 8 }}>
        {[
          {
            label: 'INVESTED',
            value: invested > 0 ? fUsd(invested) : '—',
            sub:   `${heldCount} of ${holdings.length} positions`,
            color: '#7c9fd4',
          },
          {
            label: 'PORTFOLIO VALUE',
            value: value > 0 ? fUsd(value) : '—',
            sub:   'at live prices',
            color: '#c0cfe0',
          },
          {
            label: 'TOTAL PnL',
            value: invested > 0 ? fUsd(pnlUsd, true) : '—',
            sub:   invested > 0 ? fPct(pnlPct) : 'no positions yet',
            color: pnlCol(pnlUsd),
          },
        ].map(s => (
          <div key={s.label} style={{
            flex: 1,
            background: 'rgba(255,255,255,0.02)',
            border: '1px solid rgba(255,255,255,0.05)',
            borderRadius: 8, padding: '12px 16px',
          }}>
            <div style={{ color: '#2d4060', fontSize: 7, letterSpacing: '0.12em', marginBottom: 7, ...MONO }}>
              {s.label}
            </div>
            <div style={{ color: s.color, fontSize: 20, fontWeight: 800, lineHeight: 1, ...MONO }}>
              {s.value}
            </div>
            <div style={{ color: '#1e2d3d', fontSize: 8, marginTop: 6, ...MONO }}>
              {s.sub}
            </div>
          </div>
        ))}
      </div>

      {/* ── Flash banner ── */}
      {flash && (
        <div style={{
          padding: '8px 14px',
          background: flash.ok ? 'rgba(0,212,138,0.06)' : 'rgba(239,68,68,0.06)',
          border: `1px solid ${flash.ok ? 'rgba(0,212,138,0.16)' : 'rgba(239,68,68,0.16)'}`,
          borderRadius: 5, fontSize: 10, ...MONO,
          color: flash.ok ? '#00d48a' : '#ef4444',
        }}>
          {flash.ok ? '✓' : '✗'} {flash.msg}
        </div>
      )}

      {/* ── Allocation Advice ── */}
      <div style={{
        background: 'rgba(255,255,255,0.018)',
        border: '1px solid rgba(255,255,255,0.06)',
        borderRadius: 8, padding: '14px 16px',
      }}>
        <div style={{ color: '#3d5a78', fontSize: 8, letterSpacing: '0.12em', marginBottom: 12, ...MONO, fontWeight: 700 }}>
          ALLOCATION ADVICE
        </div>

        {/* Budget input row */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ color: '#4d5a6e', fontSize: 9, ...MONO }}>BUDGET</span>

          {/* Joined $ + input */}
          <div style={{ display: 'flex' }}>
            <span style={{
              padding: '4px 7px',
              background: 'rgba(255,255,255,0.03)',
              border: '1px solid rgba(255,255,255,0.08)',
              borderRight: 'none',
              borderRadius: '4px 0 0 4px',
              color: '#3d5a78', fontSize: 10, ...MONO,
            }}>$</span>
            <input
              type="number"
              value={budget}
              onChange={e => setBudget(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && getAdvice()}
              style={{
                width: 72, padding: '4px 8px',
                background: 'rgba(255,255,255,0.03)',
                border: '1px solid rgba(255,255,255,0.08)',
                borderLeft: 'none',
                borderRadius: '0 4px 4px 0',
                color: '#c0cfe0',
                fontFamily: 'JetBrains Mono, monospace', fontSize: 11,
                outline: 'none',
              }}
            />
          </div>

          <button
            onClick={getAdvice}
            disabled={advLoading}
            style={{
              padding: '4px 12px',
              background: advLoading ? 'transparent' : 'rgba(0,212,138,0.07)',
              border: '1px solid rgba(0,212,138,0.25)',
              borderRadius: 4,
              color: advLoading ? '#2d4060' : '#00d48a',
              cursor: advLoading ? 'default' : 'pointer',
              fontFamily: 'JetBrains Mono, monospace',
              fontSize: 8, fontWeight: 700, letterSpacing: '0.1em',
            }}
          >
            {advLoading ? 'COMPUTING…' : 'GET ADVICE'}
          </button>
        </div>

        {/* Advice chips */}
        {advice && advice.length > 0 && (
          <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <span style={{ color: '#2d4060', fontSize: 8, ...MONO }}>→</span>

            {advice.map(a => (
              <button
                key={a.symbol}
                onClick={() => applyChip(a)}
                title={`Target ${a.target_pct}% · current ${a.current_pct.toFixed(1)}% · gap ${a.gap_pct.toFixed(1)}%`}
                style={{
                  ...MONO, fontSize: 9, fontWeight: 700,
                  padding: '3px 9px', borderRadius: 3, cursor: 'pointer',
                  background: 'rgba(0,212,138,0.06)',
                  border: '1px solid rgba(0,212,138,0.16)',
                  color: '#00d48a',
                }}
              >
                {a.symbol}
                <span style={{ color: '#2d4060', fontWeight: 400, marginLeft: 4 }}>
                  ${a.suggested_usd.toFixed(0)}
                </span>
              </button>
            ))}

            {/* Apply all */}
            <button
              onClick={applyAll}
              style={{
                ...MONO, fontSize: 8, fontWeight: 700,
                padding: '3px 9px', borderRadius: 3, cursor: 'pointer',
                background: 'rgba(124,159,212,0.06)',
                border: '1px solid rgba(124,159,212,0.18)',
                color: '#7c9fd4',
              }}
            >
              APPLY ALL
            </button>

            <span style={{ color: '#1e2d3d', fontSize: 8, ...MONO }}>
              click to pre-fill · ↵ to buy
            </span>
          </div>
        )}

        {advice && advice.length === 0 && (
          <div style={{ marginTop: 10, color: '#2d4060', fontSize: 9, ...MONO }}>
            Portfolio balanced — all positions at target weight
          </div>
        )}
      </div>

      {/* ── Holdings table ── */}
      <HoldingsTable
        holdings={holdings}
        isLoading={spotQ.isLoading}
        learningData={learningData}
        heldCount={heldCount}
        buyBusy={buyBusy}
        sellBusy={sellBusy}
        buyAmts={buyAmts}
        setBuyAmts={setBuyAmts}
        signalData={signalData}
        doBuy={doBuy}
        doSell={doSell}
      />

      {/* ── Transaction History ── */}
      <div style={{
        background: 'rgba(255,255,255,0.015)',
        border: '1px solid rgba(255,255,255,0.05)',
        borderRadius: 8, overflow: 'hidden',
      }}>
        <div style={{
          padding: '14px 16px',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          borderBottom: histQ.data && histQ.data.length > 0 ? '1px solid rgba(255,255,255,0.04)' : 'none',
        }}>
          <span style={{ color: '#3d5a78', fontSize: 8, letterSpacing: '0.12em', ...MONO, fontWeight: 700 }}>
            TRANSACTION HISTORY
          </span>
          {histQ.data && histQ.data.length > 0 && (
            <span style={{ color: '#2d4060', fontSize: 8, ...MONO }}>
              {histQ.data.length} entries
            </span>
          )}
        </div>

        {!histQ.data || histQ.data.length === 0 ? (
          <div style={{ padding: '8px 16px 14px', color: '#1e2d3d', fontSize: 9, ...MONO }}>
            No transactions yet — enter a budget above, click GET ADVICE, then BUY
          </div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', ...MONO }}>
            <thead>
              <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                {[
                  { l: 'DATE',   a: 'left'  },
                  { l: 'TOKEN',  a: 'left'  },
                  { l: 'SIDE',   a: 'left'  },
                  { l: 'USD',    a: 'right' },
                  { l: 'TOKENS', a: 'right' },
                  { l: 'PRICE',  a: 'right' },
                  { l: 'TX',     a: 'left'  },
                ].map(c => (
                  <th key={c.l} style={{
                    padding: '8px 12px',
                    textAlign: c.a as 'left' | 'right',
                    color: '#1e2d3d', fontSize: 7, letterSpacing: '0.1em', fontWeight: 700,
                  }}>
                    {c.l}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {histQ.data.slice(0, 30).map(tx => (
                <tr key={tx.id} style={{ borderTop: '1px solid rgba(255,255,255,0.03)' }}>
                  <td style={{ padding: '8px 12px', color: '#4d5a6e', fontSize: 9 }}>
                    {relTime(tx.ts_utc)}
                  </td>
                  <td style={{ padding: '8px 12px', color: '#c0cfe0', fontWeight: 700, fontSize: 10 }}>
                    {tx.symbol}
                  </td>
                  <td style={{ padding: '8px 12px' }}>
                    <span style={{
                      fontSize: 8, fontWeight: 700, letterSpacing: '0.06em',
                      color: tx.side === 'BUY' ? '#00d48a' : '#ef4444',
                    }}>
                      {tx.side}
                    </span>
                  </td>
                  <td style={{ padding: '8px 12px', textAlign: 'right', color: '#8a9ab0', fontSize: 9 }}>
                    ${tx.amount_usd.toFixed(2)}
                  </td>
                  <td style={{ padding: '8px 12px', textAlign: 'right', color: '#4d5a6e', fontSize: 9 }}>
                    {tx.token_amount.toFixed(4)}
                  </td>
                  <td style={{ padding: '8px 12px', textAlign: 'right', color: '#4d5a6e', fontSize: 9 }}>
                    {fPrice(tx.price_usd)}
                  </td>
                  <td style={{ padding: '8px 12px' }}>
                    {tx.dry_run ? (
                      <span style={{ color: '#f59e0b', fontSize: 8 }}>PAPER</span>
                    ) : (
                      <span style={{ color: '#2d4060', fontSize: 8 }} title={tx.tx_sig ?? ''}>
                        {(tx.tx_sig ?? '').slice(0, 8)}…
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* ── Learning System & Analytics ── */}
      <AnalyticsPanel
        signalData={signalData}
        an={an}
        learningData={learningData}
      />

    </div>
  )
}
