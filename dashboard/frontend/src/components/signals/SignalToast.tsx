/**
 * SignalToast — real-time ALERT notifications via WebSocket.
 *
 * Lives in Shell (always mounted). Subscribes to the global signalSocket.
 * When a new ALERT fires (score ≥ threshold, not DRY_RUN), shows a toast
 * top-right that auto-dismisses after 8s. Max 3 toasts stacked at once.
 * Clicking navigates to /symbol/:symbol.
 */
import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { signalSocket } from '../../ws'

// ── types ─────────────────────────────────────────────────────────────────────

interface ToastSignal {
  id: string          // unique per toast
  signal_id: number
  symbol: string
  score: number
  decision: string
  regime_label: string | null
  conviction: number | null
  ts_utc: string
}

// ── helpers ───────────────────────────────────────────────────────────────────

function convLabel(c: number | null) {
  if (c === 3) return 'A'
  if (c === 2) return 'B'
  if (c === 1) return 'C'
  return null
}

function scoreColor(s: number) {
  if (s >= 85) return 'var(--green)'
  if (s >= 75) return 'var(--amber)'
  return 'var(--text)'
}

function regimeShort(label: string | null) {
  if (!label) return ''
  return label.replace(/_/g, ' ').toLowerCase()
}

// Track signal IDs we've already toasted to avoid duplicates on reconnect
const _seen = new Set<number>()
// Don't toast signals older than 2 minutes (avoid toasting backfilled history on connect)
const _MAX_AGE_MS = 2 * 60 * 1000

// ── single toast card ─────────────────────────────────────────────────────────

function Toast({
  toast,
  onDismiss,
  idx,
}: {
  toast: ToastSignal
  onDismiss: (id: string) => void
  idx: number
}) {
  const navigate = useNavigate()
  const conv = convLabel(toast.conviction)
  const isElite = toast.score >= 85

  return (
    <div
      onClick={() => { navigate(`/symbol/${toast.symbol}`); onDismiss(toast.id) }}
      style={{
        position: 'relative',
        width: 300,
        background: 'var(--surface)',
        border: `1px solid ${isElite ? 'var(--green)' : 'var(--border)'}`,
        borderLeft: `3px solid ${scoreColor(toast.score)}`,
        borderRadius: 6,
        padding: '10px 12px',
        cursor: 'pointer',
        boxShadow: isElite
          ? '0 4px 24px rgba(0,212,138,0.18)'
          : '0 4px 16px rgba(0,0,0,0.5)',
        animation: 'toastSlideIn 0.2s ease-out',
        marginBottom: idx < 2 ? 8 : 0,
        transition: 'box-shadow 0.15s',
        userSelect: 'none',
      }}
      onMouseEnter={e => (e.currentTarget.style.boxShadow = '0 4px 24px rgba(0,212,138,0.28)')}
      onMouseLeave={e => (e.currentTarget.style.boxShadow = isElite ? '0 4px 24px rgba(0,212,138,0.18)' : '0 4px 16px rgba(0,0,0,0.5)')}
    >
      {/* Dismiss button */}
      <button
        onClick={e => { e.stopPropagation(); onDismiss(toast.id) }}
        style={{
          position: 'absolute', top: 6, right: 8,
          background: 'none', border: 'none',
          color: 'var(--dim)', fontSize: 12, cursor: 'pointer',
          lineHeight: 1, padding: '2px 4px',
        }}
      >
        ✕
      </button>

      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 6 }}>
        {/* Pulsing dot */}
        <span style={{
          width: 6, height: 6, borderRadius: '50%',
          background: 'var(--green)',
          boxShadow: '0 0 8px var(--green)',
          flexShrink: 0,
          animation: 'toastPulse 1.5s ease-in-out infinite',
          display: 'inline-block',
        }} />

        <span style={{
          fontFamily: 'JetBrains Mono, monospace',
          fontWeight: 800, fontSize: 15,
          color: 'var(--text)',
        }}>
          ${toast.symbol}
        </span>

        {/* ALERT badge */}
        <span style={{
          fontSize: 9, fontWeight: 700, letterSpacing: '0.08em',
          padding: '1px 6px', borderRadius: 2,
          background: 'rgba(0,212,138,0.12)',
          color: 'var(--green)',
          border: '1px solid rgba(0,212,138,0.25)',
          fontFamily: 'JetBrains Mono, monospace',
        }}>
          {isElite ? '🔥 ALERT' : 'ALERT'}
        </span>

        {conv && (
          <span style={{
            fontSize: 9, fontWeight: 700, letterSpacing: '0.06em',
            padding: '1px 5px', borderRadius: 2,
            background: conv === 'A' ? '#39d35322' : '#f0a50022',
            color: conv === 'A' ? 'var(--green)' : 'var(--amber)',
            fontFamily: 'JetBrains Mono, monospace',
          }}>
            {conv}
          </span>
        )}
      </div>

      {/* Score bar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 5 }}>
        <div style={{
          flex: 1, height: 3, background: 'var(--surface2)', borderRadius: 2, overflow: 'hidden',
        }}>
          <div style={{
            height: '100%',
            width: `${Math.min(toast.score, 100)}%`,
            background: scoreColor(toast.score),
            borderRadius: 2,
            transition: 'width 0.3s ease',
          }} />
        </div>
        <span style={{
          fontFamily: 'JetBrains Mono, monospace',
          fontSize: 13, fontWeight: 700,
          color: scoreColor(toast.score),
          flexShrink: 0,
        }}>
          {toast.score.toFixed(0)}
        </span>
      </div>

      {/* Regime + action hint */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: 10, color: 'var(--muted)' }}>
          {toast.regime_label ? regimeShort(toast.regime_label) : 'regime unknown'}
        </span>
        <span style={{ fontSize: 9, color: 'var(--dim)', letterSpacing: '0.04em' }}>
          tap for history →
        </span>
      </div>

      {/* Auto-dismiss progress bar */}
      <div style={{
        position: 'absolute', bottom: 0, left: 0, right: 0,
        height: 2, borderRadius: '0 0 6px 6px', overflow: 'hidden',
      }}>
        <div style={{
          height: '100%',
          background: 'var(--green)',
          opacity: 0.35,
          animation: 'toastProgress 8s linear forwards',
        }} />
      </div>
    </div>
  )
}

// ── main component ────────────────────────────────────────────────────────────

export function SignalToast() {
  const [toasts, setToasts] = useState<ToastSignal[]>([])

  const dismiss = useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id))
  }, [])

  useEffect(() => {
    const unsubFn = signalSocket.subscribe((msg: unknown) => {
      const m = msg as { type: string; data?: Record<string, unknown> }
      if (m.type !== 'signal' || !m.data) return

      const sig = m.data
      const decision = String(sig.decision ?? '')
      const score    = Number(sig.score_total ?? 0)
      const sigId    = Number(sig.id ?? 0)

      // Only real ALERTs with meaningful scores
      const isAlert = decision.includes('ALERT') && !decision.includes('DRY')
      if (!isAlert || score < 70) return

      // Deduplicate
      if (_seen.has(sigId)) return
      _seen.add(sigId)

      // Skip old signals (backfilled on WS reconnect)
      const tsUtc = String(sig.ts_utc ?? '')
      const age = tsUtc
        ? Date.now() - new Date(tsUtc + (tsUtc.endsWith('Z') ? '' : 'Z')).getTime()
        : 0
      if (age > _MAX_AGE_MS) return

      const toast: ToastSignal = {
        id:          `${sigId}-${Date.now()}`,
        signal_id:   sigId,
        symbol:      String(sig.symbol ?? ''),
        score,
        decision,
        regime_label: sig.regime_label ? String(sig.regime_label) : null,
        conviction:   sig.conviction != null ? Number(sig.conviction) : null,
        ts_utc:       tsUtc,
      }

      // Max 3 toasts stacked
      setToasts(prev => [toast, ...prev].slice(0, 3))

      // Auto-dismiss after 8s
      setTimeout(() => dismiss(toast.id), 8000)
    })

    return () => { unsubFn() }
  }, [dismiss])

  if (toasts.length === 0) return null

  return (
    <>
      <style>{`
        @keyframes toastSlideIn {
          from { opacity: 0; transform: translateX(20px); }
          to   { opacity: 1; transform: translateX(0); }
        }
        @keyframes toastPulse {
          0%, 100% { opacity: 1; box-shadow: 0 0 8px var(--green); }
          50%       { opacity: 0.5; box-shadow: 0 0 3px var(--green); }
        }
        @keyframes toastProgress {
          from { width: 100%; }
          to   { width: 0%; }
        }
      `}</style>

      <div style={{
        position: 'fixed',
        top: 20,
        right: 20,
        zIndex: 950,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'flex-end',
        pointerEvents: 'none',
      }}>
        {toasts.map((t, idx) => (
          <div key={t.id} style={{ pointerEvents: 'all' }}>
            <Toast toast={t} onDismiss={dismiss} idx={idx} />
          </div>
        ))}
      </div>
    </>
  )
}
