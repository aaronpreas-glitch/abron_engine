/**
 * TradeToast — real-time trade open/close notifications via WebSocket.
 *
 * Subscribes to signalSocket for "trade_open" and "trade_close" events
 * (distinct from "signal" events). Shows compact toasts bottom-right,
 * green for profitable closes, red for losses, blue for opens.
 * Auto-dismisses after 6s.
 */
import { useEffect, useState, useCallback } from 'react'
import { signalSocket } from '../../ws'

// ── types ─────────────────────────────────────────────────────────────────────

export interface TradeNotification {
  id:          string
  event:       'trade_open' | 'trade_close'
  mode:        string        // SCALP | SWING | SPOT
  symbol:      string
  side:        string        // LONG | SHORT
  entry_price: number
  exit_price?: number | null
  pnl_pct?:   number | null
  exit_reason?: string | null
  size_usd?:  number | null
  leverage?:  number | null
  ts:          string
}

// ── helpers ───────────────────────────────────────────────────────────────────

const MONO: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }

function pnlColor(pnl: number | null | undefined) {
  if (pnl == null) return 'var(--text)'
  return pnl >= 0 ? 'var(--green)' : 'var(--red)'
}

function modeColor(mode: string) {
  if (mode === 'SCALP') return '#a78bfa'   // purple
  if (mode === 'SWING') return '#38bdf8'   // blue
  return '#f0a500'                          // amber for SPOT
}

function exitReasonLabel(reason: string | null | undefined) {
  if (!reason) return ''
  if (reason === 'STOP_LOSS')  return '🛑 STOP'
  if (reason === 'TP1')        return '🎯 TP1'
  if (reason === 'TP2')        return '🎯 TP2'
  if (reason === 'TIME_LIMIT') return '⏱ TIME'
  if (reason === 'FORCE_CLOSE') return '⚡ FORCE'
  return reason
}

const _seenIds = new Set<string>()

// ── single trade toast card ───────────────────────────────────────────────────

function TradeCard({
  n,
  onDismiss,
}: {
  n: TradeNotification
  onDismiss: (id: string) => void
}) {
  const isClose  = n.event === 'trade_close'
  const isWin    = isClose && (n.pnl_pct ?? 0) >= 0
  const mc       = modeColor(n.mode)
  const borderC  = isClose ? (isWin ? 'var(--green)' : 'var(--red)') : mc

  return (
    <div style={{
      position: 'relative',
      width: 260,
      background: 'var(--surface)',
      border: `1px solid ${borderC}44`,
      borderLeft: `3px solid ${borderC}`,
      borderRadius: 6,
      padding: '9px 12px',
      marginBottom: 8,
      animation: 'tradeSlideIn 0.18s ease-out',
      boxShadow: `0 4px 20px rgba(0,0,0,0.45)`,
    }}>
      {/* Dismiss */}
      <button
        onClick={() => onDismiss(n.id)}
        style={{
          position: 'absolute', top: 5, right: 7,
          background: 'none', border: 'none',
          color: 'var(--dim)', fontSize: 11, cursor: 'pointer', padding: '2px 4px',
        }}
      >✕</button>

      {/* Top row: mode badge + symbol + side */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 5 }}>
        <span style={{
          fontSize: 7, fontWeight: 800, letterSpacing: '0.1em',
          padding: '1px 5px', borderRadius: 2,
          background: `${mc}18`, color: mc, border: `1px solid ${mc}44`,
          ...MONO,
        }}>
          {n.mode}
        </span>

        <span style={{ fontSize: 13, fontWeight: 800, color: 'var(--text)', ...MONO }}>
          {n.symbol}
        </span>

        <span style={{
          fontSize: 8, fontWeight: 700, letterSpacing: '0.06em',
          color: n.side === 'LONG' ? 'var(--green)' : 'var(--red)',
          ...MONO,
        }}>
          {n.side}
        </span>

        {/* Open/Close badge */}
        <span style={{
          fontSize: 7, fontWeight: 700, letterSpacing: '0.08em',
          padding: '1px 5px', borderRadius: 2, marginLeft: 'auto', marginRight: 16,
          background: isClose ? `${borderC}18` : `${mc}18`,
          color: isClose ? borderC : mc,
          border: `1px solid ${isClose ? borderC : mc}44`,
          ...MONO,
        }}>
          {isClose ? 'CLOSED' : 'OPENED'}
        </span>
      </div>

      {/* Body row */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span style={{ fontSize: 10, color: 'var(--muted)', ...MONO }}>
          @ ${n.entry_price?.toLocaleString(undefined, { maximumFractionDigits: 4 })}
        </span>

        {isClose && n.exit_price != null && (
          <span style={{ fontSize: 10, color: 'var(--dim)', ...MONO }}>
            → ${n.exit_price.toLocaleString(undefined, { maximumFractionDigits: 4 })}
          </span>
        )}

        {isClose && n.pnl_pct != null && (
          <span style={{
            fontSize: 13, fontWeight: 800, marginLeft: 'auto',
            color: pnlColor(n.pnl_pct), ...MONO,
          }}>
            {n.pnl_pct > 0 ? '+' : ''}{n.pnl_pct.toFixed(2)}%
          </span>
        )}
      </div>

      {/* Exit reason + leverage */}
      {(isClose || n.leverage) && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 3 }}>
          {isClose && n.exit_reason && (
            <span style={{ fontSize: 9, color: 'var(--dim)', ...MONO }}>
              {exitReasonLabel(n.exit_reason)}
            </span>
          )}
          {n.leverage && n.leverage > 1 && (
            <span style={{ fontSize: 9, color: 'var(--dim)', ...MONO, marginLeft: 'auto' }}>
              {n.leverage}×
            </span>
          )}
        </div>
      )}

      {/* Progress bar */}
      <div style={{
        position: 'absolute', bottom: 0, left: 0, right: 0,
        height: 2, borderRadius: '0 0 6px 6px', overflow: 'hidden',
      }}>
        <div style={{
          height: '100%', background: borderC, opacity: 0.4,
          animation: 'tradeProgress 6s linear forwards',
        }} />
      </div>
    </div>
  )
}

// ── main component ────────────────────────────────────────────────────────────

export function TradeToast() {
  const [toasts, setToasts] = useState<TradeNotification[]>([])

  const dismiss = useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id))
  }, [])

  useEffect(() => {
    const unsub = signalSocket.subscribe((msg: unknown) => {
      const m = msg as { type?: string; data?: Record<string, unknown> }
      if ((m.type !== 'trade_open' && m.type !== 'trade_close') || !m.data) return

      const d   = m.data
      const uid = `${m.type}-${d.symbol}-${d.ts}`
      if (_seenIds.has(uid)) return
      _seenIds.add(uid)

      // Ignore events older than 10s (e.g. on WS reconnect replays)
      const ts  = String(d.ts ?? '')
      const age = ts ? Date.now() - new Date(ts).getTime() : 0
      if (age > 10_000) return

      const n: TradeNotification = {
        id:          uid + '-' + Date.now(),
        event:       m.type as 'trade_open' | 'trade_close',
        mode:        String(d.mode ?? ''),
        symbol:      String(d.symbol ?? ''),
        side:        String(d.side ?? ''),
        entry_price: Number(d.entry_price ?? 0),
        exit_price:  d.exit_price != null ? Number(d.exit_price) : null,
        pnl_pct:     d.pnl_pct != null ? Number(d.pnl_pct) : null,
        exit_reason: d.exit_reason ? String(d.exit_reason) : null,
        size_usd:    d.size_usd != null ? Number(d.size_usd) : null,
        leverage:    d.leverage != null ? Number(d.leverage) : null,
        ts,
      }

      setToasts(prev => [n, ...prev].slice(0, 5))
      setTimeout(() => dismiss(n.id), 6000)
    })

    return () => { unsub() }
  }, [dismiss])

  if (toasts.length === 0) return null

  return (
    <>
      <style>{`
        @keyframes tradeSlideIn {
          from { opacity: 0; transform: translateX(16px); }
          to   { opacity: 1; transform: translateX(0); }
        }
        @keyframes tradeProgress {
          from { width: 100%; }
          to   { width: 0%; }
        }
      `}</style>

      <div style={{
        position: 'fixed',
        bottom: 20,
        right: 20,
        zIndex: 940,
        display: 'flex',
        flexDirection: 'column-reverse',
        alignItems: 'flex-end',
        pointerEvents: 'none',
      }}>
        {toasts.map(n => (
          <div key={n.id} style={{ pointerEvents: 'all' }}>
            <TradeCard n={n} onDismiss={dismiss} />
          </div>
        ))}
      </div>
    </>
  )
}
