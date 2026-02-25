import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api'
import type { ConfigValues } from '../../types'
import { LoadingSpinner } from '../shared/LoadingSpinner'

interface WeeklyReport {
  lookback_days: number
  scan_runs: number
  alerts: number
  alert_rate: number
  block_rate: number
  outcomes_4h_count: number
  avg_return_4h: number
  winrate_4h: number
  p50_score: number
  p75_score: number
  p90_score: number
  current: { alert_threshold: number; regime_min_score: number; min_confidence_to_alert: string }
  recommended: { alert_threshold: number; regime_min_score: number; min_confidence_to_alert: string }
  reasons: string[]
  optimizer: Record<string, unknown> | null
}

function changed(cur: number | string, rec: number | string) {
  return String(cur) !== String(rec)
}

export function ConfigEditor() {
  const qc = useQueryClient()
  const [form, setForm] = useState<Record<string, string | number>>({})
  const [message, setMessage] = useState<{ text: string; ok: boolean } | null>(null)
  const [reportDays, setReportDays] = useState(7)

  const { data, isLoading } = useQuery<ConfigValues>({
    queryKey: ['config'],
    queryFn: () => api.get('/config').then(r => r.data),
  })

  const { data: report, isLoading: reportLoading } = useQuery<WeeklyReport>({
    queryKey: ['perf-week', reportDays],
    queryFn: () => api.get(`/performance/week?lookback_days=${reportDays}`).then(r => r.data),
    staleTime: 120_000,
  })

  useEffect(() => { if (data) setForm({ ...data as unknown as Record<string, string | number> }) }, [data])

  const mutation = useMutation({
    mutationFn: (vals: Partial<ConfigValues>) => api.post('/config', vals).then(r => r.data),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['config'] })
      setMessage({ text: res.restarted ? '✅ Config saved & engine restarted.' : '✅ Config saved (restart manually).', ok: true })
      setTimeout(() => setMessage(null), 5000)
    },
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail
      setMessage({ text: `❌ ${Array.isArray(detail) ? detail.join(', ') : detail || 'Save failed'}`, ok: false })
    },
  })

  if (isLoading) return <LoadingSpinner />

  const inputStyle: React.CSSProperties = {
    background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)',
    padding: '6px 10px', borderRadius: 3, fontFamily: 'monospace', fontSize: 13, width: '100%',
  }
  const labelStyle: React.CSSProperties = { color: 'var(--muted)', fontSize: 11, marginBottom: 4, display: 'block' }
  const btnStyle = (active: boolean): React.CSSProperties => ({
    padding: '3px 10px', fontSize: 11, cursor: 'pointer',
    background: active ? 'var(--surface2)' : 'transparent',
    border: `1px solid ${active ? 'var(--green)' : 'var(--border)'}`,
    color: active ? 'var(--green)' : 'var(--muted)',
    borderRadius: 3,
  })

  return (
    <div>
      <h2 style={{ fontSize: 14, fontWeight: 700, letterSpacing: '0.06em', marginBottom: 16 }}>⚙️ CONFIG EDITOR</h2>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, alignItems: 'start' }}>
        {/* Left: config form */}
        <div className="card">
          <div style={{ color: 'var(--muted)', fontSize: 11, marginBottom: 16 }}>
            Changes are written to .env and the engine is restarted automatically.
          </div>

          {[
            { key: 'ALERT_THRESHOLD',          label: 'Alert Threshold',         hint: '55–95  (score required to fire a BUY alert)' },
            { key: 'REGIME_MIN_SCORE',          label: 'Regime Min Score',        hint: '10–70  (minimum regime score to allow alerts)' },
            { key: 'MIN_CONFIDENCE_TO_ALERT',   label: 'Min Confidence',          hint: 'A / B / C' },
            { key: 'MAX_ALERTS_PER_CYCLE',      label: 'Max Alerts / Cycle',      hint: '1–10' },
            { key: 'PORTFOLIO_USD',             label: 'Portfolio Size (USD)',     hint: '100–1,000,000' },
          ].map(({ key, label, hint }) => (
            <div key={key} style={{ marginBottom: 14 }}>
              <label style={labelStyle}>{label} <span style={{ color: '#555' }}>// {hint}</span></label>
              <input
                style={inputStyle}
                value={String(form[key] ?? data?.[key as keyof ConfigValues] ?? '')}
                onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))}
              />
            </div>
          ))}

          {message && (
            <div style={{
              padding: '8px 12px', borderRadius: 3, marginBottom: 12, fontSize: 12,
              color: message.ok ? 'var(--green)' : 'var(--red)',
              background: message.ok ? '#1a3a22' : '#3a1a1a',
              border: `1px solid ${message.ok ? '#2d6a35' : '#6a2a2a'}`,
            }}>
              {message.text}
            </div>
          )}

          <button
            style={{
              background: 'var(--green)', color: '#000', border: 'none', padding: '8px 20px',
              borderRadius: 3, fontFamily: 'monospace', fontWeight: 700, cursor: 'pointer', fontSize: 13,
            }}
            disabled={mutation.isPending}
            onClick={() => mutation.mutate(form)}
          >
            {mutation.isPending ? 'Saving…' : 'Save & Restart Engine'}
          </button>
        </div>

        {/* Right: tuning recommendations */}
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
            <span style={{ color: 'var(--muted)', fontSize: 11, fontWeight: 700 }}>🧠 TUNING RECOMMENDATIONS</span>
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
              {[3, 7, 14].map(d => (
                <button key={d} style={btnStyle(reportDays === d)} onClick={() => setReportDays(d)}>{d}d</button>
              ))}
            </div>
          </div>

          {reportLoading ? <LoadingSpinner /> : report && (
            <>
              {/* Stats strip */}
              <div className="card" style={{ marginBottom: 10 }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '6px 4px', fontSize: 11 }}>
                  <div>
                    <div style={{ color: 'var(--muted)', fontSize: 10 }}>ALERTS</div>
                    <div style={{ fontWeight: 700 }}>{report.alerts}</div>
                  </div>
                  <div>
                    <div style={{ color: 'var(--muted)', fontSize: 10 }}>ALERT RATE</div>
                    <div style={{ fontWeight: 700 }}>{report.alert_rate.toFixed(1)}%</div>
                  </div>
                  <div>
                    <div style={{ color: 'var(--muted)', fontSize: 10 }}>4H WINRATE</div>
                    <div style={{
                      fontWeight: 700,
                      color: report.outcomes_4h_count === 0 ? 'var(--muted)'
                        : report.winrate_4h >= 55 ? 'var(--green)'
                        : report.winrate_4h >= 45 ? 'var(--amber)'
                        : 'var(--red)',
                    }}>
                      {report.outcomes_4h_count > 0 ? `${report.winrate_4h.toFixed(0)}%` : 'n/a'}
                    </div>
                  </div>
                  <div>
                    <div style={{ color: 'var(--muted)', fontSize: 10 }}>AVG 4H</div>
                    <div style={{
                      fontWeight: 700,
                      color: report.outcomes_4h_count === 0 ? 'var(--muted)'
                        : report.avg_return_4h >= 0 ? 'var(--green)' : 'var(--red)',
                    }}>
                      {report.outcomes_4h_count > 0
                        ? `${report.avg_return_4h >= 0 ? '+' : ''}${report.avg_return_4h.toFixed(2)}%`
                        : 'n/a'}
                    </div>
                  </div>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '6px 4px', fontSize: 11, marginTop: 8 }}>
                  <div>
                    <div style={{ color: 'var(--muted)', fontSize: 10 }}>P50 SCORE</div>
                    <div style={{ fontWeight: 700 }}>{report.p50_score.toFixed(1)}</div>
                  </div>
                  <div>
                    <div style={{ color: 'var(--muted)', fontSize: 10 }}>P75 SCORE</div>
                    <div style={{ fontWeight: 700 }}>{report.p75_score.toFixed(1)}</div>
                  </div>
                  <div>
                    <div style={{ color: 'var(--muted)', fontSize: 10 }}>P90 SCORE</div>
                    <div style={{ fontWeight: 700 }}>{report.p90_score.toFixed(1)}</div>
                  </div>
                </div>
              </div>

              {/* Param comparison */}
              <div className="card" style={{ marginBottom: 10 }}>
                <div style={{ color: 'var(--muted)', fontSize: 10, marginBottom: 8, fontWeight: 700 }}>PARAMETER COMPARISON</div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 80px 80px', gap: '4px 8px', fontSize: 11 }}>
                  <div style={{ color: 'var(--muted)', fontSize: 10 }}></div>
                  <div style={{ color: 'var(--muted)', fontSize: 10, textAlign: 'center' }}>CURRENT</div>
                  <div style={{ color: 'var(--muted)', fontSize: 10, textAlign: 'center' }}>RECOMMEND</div>

                  {[
                    { label: 'Alert Threshold', cur: report.current.alert_threshold, rec: report.recommended.alert_threshold },
                    { label: 'Regime Min Score', cur: report.current.regime_min_score, rec: report.recommended.regime_min_score },
                    { label: 'Min Confidence', cur: report.current.min_confidence_to_alert, rec: report.recommended.min_confidence_to_alert },
                  ].map(({ label, cur, rec }) => {
                    const diff = changed(cur, rec)
                    return (
                      <>
                        <div key={`${label}-l`} style={{ padding: '4px 0', borderTop: '1px solid var(--border)' }}>{label}</div>
                        <div key={`${label}-c`} style={{ padding: '4px 0', borderTop: '1px solid var(--border)', textAlign: 'center', fontWeight: 700 }}>
                          {String(cur)}
                        </div>
                        <div key={`${label}-r`} style={{
                          padding: '4px 0', borderTop: '1px solid var(--border)', textAlign: 'center',
                          fontWeight: 700,
                          color: diff ? 'var(--amber)' : 'var(--green)',
                        }}>
                          {String(rec)} {diff && '←'}
                        </div>
                      </>
                    )
                  })}
                </div>

                {/* One-click apply */}
                {(changed(report.current.alert_threshold, report.recommended.alert_threshold) ||
                  changed(report.current.regime_min_score, report.recommended.regime_min_score) ||
                  changed(report.current.min_confidence_to_alert, report.recommended.min_confidence_to_alert)) && (
                  <button
                    style={{
                      marginTop: 12, width: '100%',
                      background: 'transparent', color: 'var(--amber)',
                      border: '1px solid var(--amber)', padding: '6px 12px',
                      borderRadius: 3, fontFamily: 'monospace', fontWeight: 700,
                      cursor: 'pointer', fontSize: 11,
                    }}
                    onClick={() => {
                      setForm(f => ({
                        ...f,
                        ALERT_THRESHOLD: report.recommended.alert_threshold,
                        REGIME_MIN_SCORE: report.recommended.regime_min_score,
                        MIN_CONFIDENCE_TO_ALERT: report.recommended.min_confidence_to_alert,
                      }))
                    }}
                  >
                    ← Apply Recommendations to Form
                  </button>
                )}
              </div>

              {/* Reasoning */}
              {report.reasons.length > 0 && (
                <div className="card">
                  <div style={{ color: 'var(--muted)', fontSize: 10, marginBottom: 6, fontWeight: 700 }}>ANALYSIS</div>
                  {report.reasons.map((r, i) => (
                    <div key={i} style={{
                      fontSize: 11, color: 'var(--muted)', lineHeight: 1.5,
                      padding: '4px 0',
                      borderTop: i > 0 ? '1px solid var(--border)' : undefined,
                    }}>
                      · {r}
                    </div>
                  ))}
                  {report.optimizer && (
                    <div style={{ marginTop: 8, fontSize: 10, color: 'var(--green)' }}>
                      ✓ Optimizer active — {Number(report.optimizer.samples ?? 0)} samples evaluated
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
