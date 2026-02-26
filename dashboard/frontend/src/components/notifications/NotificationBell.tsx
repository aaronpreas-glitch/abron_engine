/**
 * NotificationBell — bell icon in the TopBar showing unread trade count.
 *
 * Clicking opens a slide-down panel showing the last 50 trade events
 * (opens + closes) in reverse chronological order. Unread badge resets
 * when the panel is opened.
 */
import { useEffect, useState, useCallback, useRef } from 'react'
import { signalSocket } from '../../ws'
import type { TradeNotification } from './TradeToast'

// ── helpers ───────────────────────────────────────────────────────────────────

const MONO: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }

function pnlColor(pnl: number | null | undefined) {
  if (pnl == null) return 'var(--muted)'
  return pnl >= 0 ? 'var(--green)' : 'var(--red)'
}

function modeColor(mode: string) {
  if (mode === 'SCALP') return '#a78bfa'
  if (mode === 'SWING') return '#38bdf8'
  return '#f0a500'
}

function exitReasonShort(r: string | null | undefined) {
  if (!r) return ''
  if (r === 'STOP_LOSS')   return 'STOP'
  if (r === 'TIME_LIMIT')  return 'TIME'
  if (r === 'FORCE_CLOSE') return 'FORCE'
  return r
}

function timeAgo(ts: string) {
  const d = Date.now() - new Date(ts).getTime()
  const s = Math.floor(d / 1000)
  if (s < 60)   return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60)   return `${m}m ago`
  const h = Math.floor(m / 60)
  return `${h}h ago`
}

const _historySeenIds = new Set<string>()

// ── notification row ──────────────────────────────────────────────────────────

function NRow({ n }: { n: TradeNotification }) {
  const isClose = n.event === 'trade_close'
  const mc      = modeColor(n.mode)
  const win     = isClose && (n.pnl_pct ?? 0) >= 0

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8,
      padding: '7px 14px',
      borderBottom: '1px solid rgba(255,255,255,0.04)',
      transition: 'background 0.1s',
    }}
      onMouseEnter={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.03)')}
      onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
    >
      {/* Mode dot */}
      <span style={{
        width: 5, height: 5, borderRadius: '50%',
        background: mc, flexShrink: 0,
        boxShadow: `0 0 5px ${mc}`,
      }} />

      {/* Symbol + side */}
      <span style={{ fontSize: 11, fontWeight: 800, color: 'var(--text)', ...MONO, minWidth: 48 }}>
        {n.symbol}
      </span>
      <span style={{
        fontSize: 8, fontWeight: 700, color: n.side === 'LONG' ? 'var(--green)' : 'var(--red)',
        ...MONO, minWidth: 32,
      }}>
        {n.side}
      </span>

      {/* Event */}
      <span style={{
        fontSize: 8, fontWeight: 700, letterSpacing: '0.06em',
        color: isClose ? (win ? 'var(--green)' : 'var(--red)') : mc,
        ...MONO, minWidth: 42,
      }}>
        {isClose ? (win ? '✓ CLOSE' : '✗ CLOSE') : '→ OPEN'}
      </span>

      {/* PnL or entry */}
      <span style={{ fontSize: 10, fontWeight: 700, color: pnlColor(n.pnl_pct), ...MONO, flex: 1 }}>
        {isClose && n.pnl_pct != null
          ? `${n.pnl_pct > 0 ? '+' : ''}${n.pnl_pct.toFixed(2)}%`
          : `$${n.entry_price.toLocaleString(undefined, { maximumFractionDigits: 2 })}`
        }
      </span>

      {/* Reason */}
      {isClose && n.exit_reason && (
        <span style={{ fontSize: 8, color: 'var(--dim)', ...MONO, minWidth: 36, textAlign: 'right' }}>
          {exitReasonShort(n.exit_reason)}
        </span>
      )}

      {/* Time */}
      <span style={{ fontSize: 8, color: 'rgba(255,255,255,0.2)', ...MONO, minWidth: 44, textAlign: 'right' }}>
        {timeAgo(n.ts)}
      </span>
    </div>
  )
}

// ── main component ────────────────────────────────────────────────────────────

export function NotificationBell() {
  const [history,  setHistory]  = useState<TradeNotification[]>([])
  const [unread,   setUnread]   = useState(0)
  const [open,     setOpen]     = useState(false)
  const panelRef = useRef<HTMLDivElement>(null)

  // Close panel when clicking outside
  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (open && panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [open])

  // Listen for trade events
  useEffect(() => {
    const unsub = signalSocket.subscribe((msg: unknown) => {
      const m = msg as { type?: string; data?: Record<string, unknown> }
      if ((m.type !== 'trade_open' && m.type !== 'trade_close') || !m.data) return

      const d   = m.data
      const uid = `${m.type}-${d.symbol}-${d.ts}`
      if (_historySeenIds.has(uid)) return
      _historySeenIds.add(uid)

      const n: TradeNotification = {
        id:          uid,
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
        ts:          String(d.ts ?? new Date().toISOString()),
      }

      setHistory(prev => [n, ...prev].slice(0, 50))
      setUnread(prev => prev + 1)
    })
    return () => { unsub() }
  }, [])

  const handleOpen = useCallback(() => {
    setOpen(o => !o)
    setUnread(0)
  }, [])

  const hasAny = history.length > 0

  return (
    <div ref={panelRef} style={{ position: 'relative' }}>
      {/* Bell button */}
      <button
        onClick={handleOpen}
        title="Trade notifications"
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          width: 28, height: 28,
          background: open ? 'rgba(255,255,255,0.07)' : 'transparent',
          border: 'none',
          borderRadius: 4,
          cursor: 'pointer',
          position: 'relative',
          transition: 'background 0.15s',
          color: hasAny ? 'rgba(255,255,255,0.65)' : 'rgba(255,255,255,0.25)',
          fontSize: 13,
        }}
        onMouseEnter={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.07)')}
        onMouseLeave={e => (e.currentTarget.style.background = open ? 'rgba(255,255,255,0.07)' : 'transparent')}
      >
        🔔
        {unread > 0 && (
          <span style={{
            position: 'absolute', top: 2, right: 2,
            minWidth: 14, height: 14,
            background: '#f04f4f',
            borderRadius: 7,
            fontSize: 7, fontWeight: 800,
            color: '#fff',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            padding: '0 2px',
            ...MONO,
            lineHeight: 1,
          }}>
            {unread > 99 ? '99+' : unread}
          </span>
        )}
      </button>

      {/* Drop-down panel */}
      {open && (
        <div style={{
          position: 'absolute',
          top: 'calc(100% + 6px)',
          right: 0,
          width: 360,
          background: 'var(--surface)',
          border: '1px solid rgba(255,255,255,0.1)',
          borderRadius: 8,
          boxShadow: '0 8px 40px rgba(0,0,0,0.6)',
          zIndex: 1000,
          overflow: 'hidden',
          animation: 'bellPanelIn 0.15s ease-out',
        }}>
          {/* Header */}
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '10px 14px 9px',
            borderBottom: '1px solid rgba(255,255,255,0.07)',
          }}>
            <span style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.14em', color: 'var(--dim)', ...MONO }}>
              TRADE NOTIFICATIONS
            </span>
            <span style={{ fontSize: 8, color: 'rgba(255,255,255,0.2)', ...MONO }}>
              {history.length} event{history.length !== 1 ? 's' : ''}
            </span>
          </div>

          {/* List */}
          <div style={{ maxHeight: 380, overflowY: 'auto' }}>
            {history.length === 0 ? (
              <div style={{
                padding: '28px 14px', textAlign: 'center',
                color: 'var(--dim)', fontSize: 10, ...MONO,
              }}>
                No trade events yet
              </div>
            ) : (
              history.map(n => <NRow key={n.id} n={n} />)
            )}
          </div>
        </div>
      )}

      <style>{`
        @keyframes bellPanelIn {
          from { opacity: 0; transform: translateY(-6px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  )
}
