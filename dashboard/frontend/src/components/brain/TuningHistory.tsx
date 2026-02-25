/**
 * TuningHistory — auto-tuner audit log panel for the Brain page.
 *
 * Reads from /api/brain/tuning-history (the tuning_log.json written by auto_tune.py).
 * Shows every weekly run with action, before/after config diff, rationale, and metrics.
 *
 * Action types:
 *   APPLIED              — changes wrote to .env + engine restarted (green border)
 *   SKIPPED_NO_CHANGE    — optimizer ran but delta was too small (gray border)
 *   SKIPPED_INSUFFICIENT_DATA — not enough data to trust recommendations (amber border)
 *   DRY_RUN              — manual --dry-run invocation (blue border)
 */
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'

// ── types ──────────────────────────────────────────────────────────────────────

interface ConfigSnapshot {
  ALERT_THRESHOLD: number
  REGIME_MIN_SCORE: number
  MIN_CONFIDENCE_TO_ALERT: string
}

interface TuningMetrics {
  scan_runs: number
  alerts: number
  outcomes_4h: number
  avg_return_4h: number
  win_rate_4h: number
  current_threshold?: number
}

interface OptimizerResult {
  samples?: number
  best_threshold?: number
  best_regime?: number
  best_confidence?: string
  avg_return_4h_pct?: number
  win_rate_4h_pct?: number
}

interface TuningEntry {
  ts_utc: string
  action: 'APPLIED' | 'SKIPPED_NO_CHANGE' | 'SKIPPED_INSUFFICIENT_DATA' | 'DRY_RUN'
  before: ConfigSnapshot | null
  after: ConfigSnapshot | null
  reasons: string[]
  metrics: TuningMetrics
  optimizer: OptimizerResult | null
}

// ── helpers ────────────────────────────────────────────────────────────────────

function actionStyle(action: TuningEntry['action']): { border: string; badge: string; badgeBg: string; label: string } {
  switch (action) {
    case 'APPLIED':
      return { border: 'var(--green)', badge: 'var(--green)', badgeBg: 'rgba(0,212,138,0.1)', label: '✓ APPLIED' }
    case 'SKIPPED_NO_CHANGE':
      return { border: 'var(--border)', badge: 'var(--muted)', badgeBg: 'var(--surface2)', label: '— NO CHANGE' }
    case 'SKIPPED_INSUFFICIENT_DATA':
      return { border: 'var(--amber)', badge: 'var(--amber)', badgeBg: 'rgba(240,165,0,0.1)', label: '⏳ INSUFFICIENT DATA' }
    case 'DRY_RUN':
      return { border: '#58a6ff', badge: '#58a6ff', badgeBg: 'rgba(88,166,255,0.1)', label: '🔍 DRY RUN' }
  }
}

function fmtTs(ts: string): string {
  try {
    const d = new Date(ts.endsWith('Z') ? ts : ts + 'Z')
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }) + ' UTC'
  } catch { return ts }
}

function ConfigDiff({ before, after }: { before: ConfigSnapshot | null; after: ConfigSnapshot | null }) {
  if (!before) return null
  const rows: { key: string; bv: string | number; av: string | number | undefined; changed: boolean }[] = [
    {
      key: 'ALERT_THRESHOLD',
      bv: before.ALERT_THRESHOLD,
      av: after?.ALERT_THRESHOLD,
      changed: after != null && after.ALERT_THRESHOLD !== before.ALERT_THRESHOLD,
    },
    {
      key: 'REGIME_MIN_SCORE',
      bv: before.REGIME_MIN_SCORE,
      av: after?.REGIME_MIN_SCORE,
      changed: after != null && after.REGIME_MIN_SCORE !== before.REGIME_MIN_SCORE,
    },
    {
      key: 'MIN_CONFIDENCE_TO_ALERT',
      bv: before.MIN_CONFIDENCE_TO_ALERT,
      av: after?.MIN_CONFIDENCE_TO_ALERT,
      changed: after != null && after.MIN_CONFIDENCE_TO_ALERT !== before.MIN_CONFIDENCE_TO_ALERT,
    },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 3, marginTop: 8 }}>
      {rows.map(r => (
        <div key={r.key} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, fontFamily: 'JetBrains Mono, monospace' }}>
          <span style={{ color: 'var(--dim)', minWidth: 180, fontSize: 10 }}>{r.key}</span>
          <span style={{ color: r.changed ? 'var(--amber)' : 'var(--muted)' }}>{r.bv}</span>
          {r.av !== undefined && r.changed && (
            <>
              <span style={{ color: 'var(--dim)' }}>→</span>
              <span style={{ color: 'var(--green)', fontWeight: 700 }}>{r.av}</span>
            </>
          )}
          {(!r.av || !r.changed) && (
            <span style={{ color: 'var(--dim)', fontSize: 9 }}>unchanged</span>
          )}
        </div>
      ))}
    </div>
  )
}

// ── single entry card ──────────────────────────────────────────────────────────

function TuningCard({ entry }: { entry: TuningEntry }) {
  const style = actionStyle(entry.action)
  const m     = entry.metrics

  return (
    <div style={{
      borderLeft: `3px solid ${style.border}`,
      background: 'var(--surface)',
      border: `1px solid var(--border)`,
      borderLeftColor: style.border,
      borderLeftWidth: 3,
      borderRadius: 6,
      padding: '12px 14px',
      marginBottom: 10,
    }}>
      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
        <span style={{
          fontSize: 9, fontWeight: 700, letterSpacing: '0.08em', padding: '2px 7px',
          borderRadius: 3, fontFamily: 'JetBrains Mono, monospace',
          color: style.badge, background: style.badgeBg,
          border: `1px solid ${style.badge}33`,
        }}>
          {style.label}
        </span>
        <span style={{ fontSize: 10, color: 'var(--dim)', marginLeft: 'auto' }}>
          {fmtTs(entry.ts_utc)}
        </span>
      </div>

      {/* Metrics row */}
      <div style={{ display: 'flex', gap: 20, fontSize: 11, marginBottom: 8, flexWrap: 'wrap' }}>
        <span style={{ color: 'var(--muted)' }}>
          <span style={{ color: 'var(--dim)', marginRight: 4 }}>outcomes</span>
          <span style={{ fontWeight: 700, color: 'var(--text)' }}>{m.outcomes_4h}</span>
        </span>
        <span style={{ color: 'var(--muted)' }}>
          <span style={{ color: 'var(--dim)', marginRight: 4 }}>win rate</span>
          <span style={{ fontWeight: 700, color: m.win_rate_4h >= 50 ? 'var(--green)' : 'var(--red)' }}>
            {m.win_rate_4h.toFixed(1)}%
          </span>
        </span>
        <span style={{ color: 'var(--muted)' }}>
          <span style={{ color: 'var(--dim)', marginRight: 4 }}>avg 4h</span>
          <span style={{ fontWeight: 700, color: m.avg_return_4h >= 0 ? 'var(--green)' : 'var(--red)' }}>
            {m.avg_return_4h >= 0 ? '+' : ''}{m.avg_return_4h.toFixed(2)}%
          </span>
        </span>
        <span style={{ color: 'var(--muted)' }}>
          <span style={{ color: 'var(--dim)', marginRight: 4 }}>scan runs</span>
          <span style={{ fontWeight: 700, color: 'var(--text)' }}>{m.scan_runs}</span>
        </span>
      </div>

      {/* Config diff */}
      {(entry.before) && (
        <ConfigDiff before={entry.before} after={entry.after} />
      )}

      {/* Reasons */}
      {entry.reasons.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <div style={{ fontSize: 9, color: 'var(--dim)', letterSpacing: '0.06em', marginBottom: 4 }}>
            RATIONALE
          </div>
          {entry.reasons.slice(0, 5).map((r, i) => (
            <div key={i} style={{ fontSize: 10, color: 'var(--muted)', lineHeight: 1.6, paddingLeft: 8 }}>
              — {r}
            </div>
          ))}
        </div>
      )}

      {/* Optimizer result if present */}
      {entry.optimizer && entry.optimizer.samples && (
        <div style={{ marginTop: 8, padding: '6px 8px', background: 'var(--surface2)', borderRadius: 3 }}>
          <span style={{ fontSize: 9, color: 'var(--dim)', letterSpacing: '0.06em' }}>OPTIMIZER </span>
          <span style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'JetBrains Mono, monospace' }}>
            {entry.optimizer.samples} samples · best threshold {entry.optimizer.best_threshold} · regime {entry.optimizer.best_regime} · conf {entry.optimizer.best_confidence}
          </span>
        </div>
      )}
    </div>
  )
}

// ── main component ─────────────────────────────────────────────────────────────

export function TuningHistory() {
  const { data: entries = [], isLoading, error } = useQuery<TuningEntry[]>({
    queryKey: ['brain-tuning-history'],
    queryFn: async () => {
      const r = await api.get('/brain/tuning-history')
      return r.data
    },
    staleTime: 60_000,
    refetchInterval: 300_000, // refresh every 5 min
  })

  return (
    <section>
      {/* Section header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <div>
          <div style={{ fontWeight: 700, fontSize: 13, letterSpacing: '0.05em' }}>
            AUTO-TUNE HISTORY
          </div>
          <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 2 }}>
            Weekly optimizer runs — every Monday 09:00 UTC
          </div>
        </div>
        <div style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--dim)' }}>
          last {entries.length} runs
        </div>
      </div>

      {isLoading && (
        <div style={{ color: 'var(--muted)', fontSize: 12, padding: '20px 0' }}>Loading tuning history…</div>
      )}

      {error && (
        <div style={{ color: 'var(--red)', fontSize: 11, padding: '12px 0' }}>
          Failed to load tuning history.
        </div>
      )}

      {!isLoading && !error && entries.length === 0 && (
        <div style={{
          border: '1px dashed var(--border)',
          borderRadius: 6,
          padding: '28px 20px',
          textAlign: 'center',
          color: 'var(--dim)',
          fontSize: 11,
          lineHeight: 1.7,
        }}>
          No auto-tune runs yet.<br />
          <span style={{ color: 'var(--muted)' }}>
            The tuner runs every Monday at 09:00 UTC.<br />
            You can also run it manually: <code style={{ color: 'var(--green)', fontSize: 10 }}>python auto_tune.py --dry-run</code>
          </span>
        </div>
      )}

      {entries.map((entry, i) => (
        <TuningCard key={`${entry.ts_utc}-${i}`} entry={entry} />
      ))}
    </section>
  )
}
