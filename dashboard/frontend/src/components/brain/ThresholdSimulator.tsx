/**
 * ThresholdSimulator — drag a slider to replay historical outcomes at any score threshold.
 * Shows how win rate and alert volume change if you raise or lower ALERT_THRESHOLD.
 * Added to Brain page after the QueueStatus section.
 */
import { useState, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'

// ── Types ──────────────────────────────────────────────────────────────────────

interface SimResult {
  threshold: number
  sim_n: number
  sim_win_rate: number
  sim_avg_ret: number
  current_threshold: number
  current_n: number
  current_win_rate: number
  current_avg_ret: number
  lookback_days: number
  horizon: number
}

// ── Helpers ────────────────────────────────────────────────────────────────────

const MONO: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }

function retColor(v: number) {
  return v > 0 ? 'var(--green)' : v < 0 ? 'var(--red)' : 'var(--muted)'
}

function winColor(wr: number) {
  return wr >= 55 ? 'var(--green)' : wr >= 45 ? 'var(--amber)' : 'var(--red)'
}

function fmtPct(v: number | null | undefined, plus = true) {
  if (v == null) return '—'
  const s = plus && v > 0 ? '+' : ''
  return `${s}${v.toFixed(2)}%`
}

function DeltaBadge({ diff, unit = '' }: { diff: number; unit?: string }) {
  if (Math.abs(diff) < 0.05) return <span style={{ fontSize: 9, color: 'var(--dim)', ...MONO }}>≈ same</span>
  const up = diff > 0
  return (
    <span style={{
      fontSize: 9, fontWeight: 700, padding: '1px 5px', borderRadius: 2,
      background: up ? 'rgba(0,212,138,0.12)' : 'rgba(248,81,73,0.12)',
      color: up ? 'var(--green)' : 'var(--red)',
      ...MONO,
    }}>
      {up ? '↑' : '↓'} {Math.abs(diff).toFixed(1)}{unit}
    </span>
  )
}

// ── StatBlock ─────────────────────────────────────────────────────────────────

function StatBlock({
  label, n, winRate, avgRet, highlight,
}: {
  label: string
  n: number
  winRate: number
  avgRet: number
  highlight?: boolean
}) {
  return (
    <div style={{
      flex: 1,
      background: highlight ? 'rgba(0,212,138,0.06)' : 'var(--surface2)',
      border: `1px solid ${highlight ? 'rgba(0,212,138,0.25)' : 'var(--border)'}`,
      borderRadius: 8,
      padding: '14px 16px',
    }}>
      <div style={{
        fontSize: 9, color: highlight ? 'var(--green)' : 'var(--dim)',
        letterSpacing: '0.15em', ...MONO, marginBottom: 12, fontWeight: 700,
      }}>
        {label}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div>
          <div style={{ fontSize: 9, color: 'var(--dim)', ...MONO, letterSpacing: '0.1em', marginBottom: 3 }}>ALERTS</div>
          <div style={{ fontSize: 22, fontWeight: 800, color: 'var(--text)', ...MONO, lineHeight: 1 }}>{n}</div>
        </div>
        <div>
          <div style={{ fontSize: 9, color: 'var(--dim)', ...MONO, letterSpacing: '0.1em', marginBottom: 3 }}>WIN RATE</div>
          <div style={{ fontSize: 18, fontWeight: 700, color: winColor(winRate), ...MONO, lineHeight: 1 }}>
            {n > 0 ? `${winRate.toFixed(1)}%` : '—'}
          </div>
        </div>
        <div>
          <div style={{ fontSize: 9, color: 'var(--dim)', ...MONO, letterSpacing: '0.1em', marginBottom: 3 }}>AVG RET</div>
          <div style={{ fontSize: 16, fontWeight: 700, color: n > 0 ? retColor(avgRet) : 'var(--dim)', ...MONO, lineHeight: 1 }}>
            {n > 0 ? fmtPct(avgRet) : '—'}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Main Component ─────────────────────────────────────────────────────────────

export function ThresholdSimulator({
  lookback, horizon,
}: { lookback: number; horizon: number }) {
  const [sliderValue, setSliderValue] = useState(72)
  const [debouncedValue, setDebouncedValue] = useState(72)

  // Debounce slider → only query after 200ms of no movement
  useEffect(() => {
    const t = setTimeout(() => setDebouncedValue(sliderValue), 200)
    return () => clearTimeout(t)
  }, [sliderValue])

  const { data, isFetching } = useQuery<SimResult>({
    queryKey: ['brain-threshold-sim', debouncedValue, lookback, horizon],
    queryFn: () =>
      api.get(`/brain/threshold-sim?threshold=${debouncedValue}&lookback_days=${lookback}&horizon=${horizon}`)
        .then(r => r.data),
    staleTime: 60_000,
  })

  const isLower = sliderValue < (data?.current_threshold ?? 72)
  const isHigher = sliderValue > (data?.current_threshold ?? 72)
  const isSame = sliderValue === (data?.current_threshold ?? 72)

  return (
    <div>
      {/* Header */}
      <div style={{
        fontSize: 9, fontWeight: 600, letterSpacing: '0.18em',
        color: 'var(--dim)', ...MONO, textTransform: 'uppercase', marginBottom: 14,
      }}>
        Score Threshold Simulator
      </div>

      {/* Slider */}
      <div style={{ marginBottom: 18 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <span style={{ fontSize: 10, color: 'var(--muted)', ...MONO }}>
            Threshold: <span style={{
              fontSize: 16, fontWeight: 800, color: 'var(--text)',
              transition: 'color 0.1s',
            }}>{sliderValue}</span>
          </span>
          <span style={{ fontSize: 10, color: 'var(--dim)', ...MONO }}>
            {isLower ? '← more permissive' : isHigher ? 'more selective →' : '● at current config'}
          </span>
        </div>

        <input
          type="range"
          min={50}
          max={99}
          step={1}
          value={sliderValue}
          onChange={e => setSliderValue(Number(e.target.value))}
          style={{
            width: '100%',
            accentColor: 'var(--green)',
            cursor: 'pointer',
            height: 4,
          }}
        />

        {/* Tick marks */}
        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4 }}>
          {[50, 60, 70, 80, 90, 99].map(v => (
            <span key={v} style={{ fontSize: 8, color: 'var(--dim)', ...MONO }}>{v}</span>
          ))}
        </div>
      </div>

      {/* Comparison blocks */}
      {data ? (
        <>
          <div style={{ display: 'flex', gap: 12, marginBottom: 14 }}>
            <StatBlock
              label={`CURRENT (≥${data.current_threshold})`}
              n={data.current_n}
              winRate={data.current_win_rate}
              avgRet={data.current_avg_ret}
            />
            <div style={{
              display: 'flex', flexDirection: 'column', alignItems: 'center',
              justifyContent: 'center', gap: 8, minWidth: 60,
            }}>
              {!isSame && (
                <>
                  <DeltaBadge diff={data.sim_win_rate - data.current_win_rate} unit="%" />
                  <DeltaBadge diff={data.sim_avg_ret - data.current_avg_ret} unit="%" />
                  <span style={{ fontSize: 9, color: 'var(--dim)', ...MONO }}>
                    {data.sim_n - data.current_n > 0 ? '+' : ''}{data.sim_n - data.current_n} alerts
                  </span>
                </>
              )}
              {isSame && <span style={{ fontSize: 11, color: 'var(--dim)' }}>↔</span>}
            </div>
            <StatBlock
              label={`SIMULATED (≥${sliderValue})`}
              n={data.sim_n}
              winRate={data.sim_win_rate}
              avgRet={data.sim_avg_ret}
              highlight={!isSame && data.sim_win_rate > data.current_win_rate}
            />
          </div>

          {/* Insight note */}
          {!isSame && data.sim_n > 0 && (
            <div style={{
              fontSize: 10, color: 'var(--muted)', ...MONO, lineHeight: 1.5,
              padding: '8px 10px', background: 'var(--surface2)', borderRadius: 4,
            }}>
              {isHigher
                ? `Raising to ≥${sliderValue} filters down to ${data.sim_n} alerts (${Math.abs(data.sim_n - data.current_n)} fewer). `
                : `Lowering to ≥${sliderValue} expands to ${data.sim_n} alerts (+${data.sim_n - data.current_n}). `}
              {data.sim_win_rate > data.current_win_rate
                ? `Win rate improves by ${(data.sim_win_rate - data.current_win_rate).toFixed(1)}pp.`
                : data.sim_win_rate < data.current_win_rate
                ? `Win rate drops by ${(data.current_win_rate - data.sim_win_rate).toFixed(1)}pp.`
                : 'Win rate unchanged.'}
            </div>
          )}
          {data.sim_n === 0 && (
            <div style={{ fontSize: 10, color: 'var(--amber)', ...MONO }}>
              ⚠ No historical outcomes found at threshold ≥{sliderValue} — too restrictive.
            </div>
          )}
          {isFetching && (
            <div style={{ fontSize: 9, color: 'var(--dim)', ...MONO, marginTop: 6 }}>updating…</div>
          )}
        </>
      ) : (
        <div style={{ color: 'var(--dim)', fontSize: 11, ...MONO }}>
          {isFetching ? 'Loading simulation…' : 'Move the slider to simulate.'}
        </div>
      )}

      <div style={{ marginTop: 10, fontSize: 9, color: 'var(--dim)', ...MONO }}>
        Based on {lookback}d of alert_outcomes · horizon {horizon}h · no engine changes made
      </div>
    </div>
  )
}
