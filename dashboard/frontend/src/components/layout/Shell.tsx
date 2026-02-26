import { useEffect, useState } from 'react'
import { Outlet, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { RiskBanner } from './RiskBanner'
import { Sidebar } from './Sidebar'
import { AiChat } from '../ai/AiChat'
import { SignalToast } from '../signals/SignalToast'
import { TradeToast } from '../notifications/TradeToast'
import { NotificationBell } from '../notifications/NotificationBell'
import { api } from '../../api'

// ── Types ─────────────────────────────────────────────────────────────────────

interface SnapshotData {
  regime: { regime_label: string; regime_score: number } | null
}

interface ExecStatus {
  open_positions: number
  enabled: boolean
  dry_run: boolean
  total_closed?: number
}

interface PerpStatus {
  open_positions: number
  enabled: boolean
  dry_run: boolean
  total_closed?: number
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const MONO: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }

function regimeColor(label: string) {
  if (!label) return 'rgba(255,255,255,0.35)'
  if (label.includes('RISK_ON') || label.includes('BULL')) return '#00d48a'
  if (label.includes('RISK_OFF') || label.includes('BEAR')) return '#f04f4f'
  return '#f0a500'
}

// Derive bot status: GREEN = running+enabled, YELLOW = loaded but disabled, RED = offline
type BotState = 'running' | 'idle' | 'offline'

function botState(status: ExecStatus | PerpStatus | undefined): BotState {
  if (!status || typeof status !== 'object' || !('enabled' in status)) return 'offline'
  if (status.enabled) return 'running'
  return 'idle'
}

function botColor(state: BotState) {
  if (state === 'running') return '#00d48a'
  if (state === 'idle')    return '#f0a500'
  return '#f84951'
}

function botGlow(state: BotState) {
  if (state === 'running') return '0 0 8px #00d48a, 0 0 16px rgba(0,212,138,0.3)'
  if (state === 'idle')    return '0 0 6px #f0a500'
  return '0 0 6px #f84951'
}

function botLabel(state: BotState, dry_run: boolean) {
  if (state === 'running') return dry_run ? 'PAPER' : 'LIVE'
  if (state === 'idle')    return 'IDLE'
  return 'OFF'
}

// ── Bot Status Pill ────────────────────────────────────────────────────────────

function BotPill({
  label,
  state,
  dry_run,
  open,
  closed,
  onClick,
}: {
  label: string
  state: BotState
  dry_run: boolean
  open: number
  closed?: number
  onClick?: () => void
}) {
  const color = botColor(state)
  const glow  = botGlow(state)
  const tag   = botLabel(state, dry_run)
  const isRunning = state === 'running'

  return (
    <div
      onClick={onClick}
      style={{
        display: 'flex', alignItems: 'center', gap: 6,
        padding: '0 12px', height: '100%',
        borderRight: '1px solid rgba(255,255,255,0.06)',
        cursor: onClick ? 'pointer' : undefined,
        transition: 'background 0.15s',
      }}
      onMouseEnter={e => onClick && (e.currentTarget.style.background = 'rgba(255,255,255,0.04)')}
      onMouseLeave={e => onClick && (e.currentTarget.style.background = 'transparent')}
    >
      {/* Pulsing dot */}
      <span style={{
        width: 7, height: 7, borderRadius: '50%',
        background: color,
        boxShadow: glow,
        flexShrink: 0,
        display: 'inline-block',
        animation: isRunning ? 'pulse-glow 2s ease-in-out infinite' : undefined,
      }} />

      {/* Label + mode tag */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 0, lineHeight: 1 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <span style={{ fontSize: 9, fontWeight: 700, color: 'rgba(255,255,255,0.5)', letterSpacing: '0.12em', ...MONO }}>
            {label}
          </span>
          <span style={{
            fontSize: 7, fontWeight: 800, padding: '1px 4px', borderRadius: 2, letterSpacing: '0.1em',
            background: `${color}18`,
            color,
            border: `1px solid ${color}44`,
            ...MONO,
          }}>
            {tag}
          </span>
        </div>
        <span style={{ fontSize: 8.5, fontWeight: 700, color: isRunning ? color : 'rgba(255,255,255,0.22)', ...MONO, marginTop: 1 }}>
          {open > 0 ? `${open} open` : closed != null ? `${closed} trades` : '—'}
        </span>
      </div>
    </div>
  )
}

// ── Top Status Bar ─────────────────────────────────────────────────────────────

function TopBar() {
  const navigate = useNavigate()
  const [utc, setUtc] = useState('')

  useEffect(() => {
    function tick() {
      const now = new Date()
      const h = now.getUTCHours().toString().padStart(2, '0')
      const m = now.getUTCMinutes().toString().padStart(2, '0')
      const s = now.getUTCSeconds().toString().padStart(2, '0')
      setUtc(`${h}:${m}:${s} UTC`)
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])

  const { data: snap } = useQuery<SnapshotData>({
    queryKey: ['shell-snapshot'],
    queryFn: () => api.get('/snapshot').then(r => r.data),
    refetchInterval: 30_000,
    staleTime: 25_000,
  })

  const { data: exec } = useQuery<ExecStatus>({
    queryKey: ['shell-exec-status'],
    queryFn: () => api.get('/executor/status').then(r => r.data),
    refetchInterval: 20_000,
    staleTime: 15_000,
  })

  const { data: perps } = useQuery<PerpStatus>({
    queryKey: ['shell-perps-status'],
    queryFn: () => api.get('/perps/status').then(r => r.data),
    refetchInterval: 20_000,
    staleTime: 15_000,
  })

  const regime    = snap?.regime
  const rc        = regime ? regimeColor(regime.regime_label) : 'rgba(255,255,255,0.35)'
  const spotState = botState(exec && typeof exec === 'object' && 'enabled' in exec ? exec : undefined)
  const perpState = botState(perps && typeof perps === 'object' && 'enabled' in perps ? perps : undefined)

  // Both bots running = full green banner tint
  const bothRunning = spotState === 'running' && perpState === 'running'

  return (
    <div style={{
      height: 36,
      background: bothRunning
        ? 'rgba(0,212,138,0.04)'
        : 'rgba(4,6,12,0.92)',
      backdropFilter: 'blur(20px)',
      borderBottom: bothRunning
        ? '1px solid rgba(0,212,138,0.12)'
        : '1px solid rgba(255,255,255,0.06)',
      display: 'flex',
      alignItems: 'center',
      paddingLeft: 14,
      paddingRight: 14,
      gap: 0,
      flexShrink: 0,
      overflow: 'hidden',
      transition: 'background 0.5s, border-color 0.5s',
    }}>

      {/* Brand */}
      <span style={{
        fontSize: 9.5, fontWeight: 800, color: 'rgba(255,255,255,0.35)',
        letterSpacing: '0.18em', ...MONO, paddingRight: 12,
        borderRight: '1px solid rgba(255,255,255,0.07)',
        flexShrink: 0,
      }}>
        ABRONS
      </span>

      {/* Regime */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 5,
        padding: '0 12px', height: '100%',
        borderRight: '1px solid rgba(255,255,255,0.06)',
      }}>
        <span style={{
          width: 5, height: 5, borderRadius: '50%',
          background: rc, boxShadow: `0 0 5px ${rc}`,
          flexShrink: 0,
        }} />
        <span style={{ fontSize: 9, fontWeight: 700, color: rc, letterSpacing: '0.06em', ...MONO }}>
          {regime?.regime_label?.replace(/_/g, ' ') || '—'}
        </span>
        {regime?.regime_score != null && (
          <span style={{ fontSize: 7.5, color: 'rgba(255,255,255,0.2)', ...MONO }}>
            {regime.regime_score.toFixed(0)}
          </span>
        )}
      </div>

      {/* Spot bot status */}
      <BotPill
        label="SPOT"
        state={spotState}
        dry_run={exec?.dry_run ?? true}
        open={exec?.open_positions ?? 0}
        closed={exec?.total_closed}
        onClick={() => navigate(exec?.dry_run !== false ? '/trading/spot-paper' : '/trading/spot-live')}
      />

      {/* Perps bot status */}
      <BotPill
        label="PERPS"
        state={perpState}
        dry_run={perps?.dry_run ?? true}
        open={perps?.open_positions ?? 0}
        closed={perps?.total_closed}
        onClick={() => navigate(perps?.dry_run !== false ? '/trading/perps-paper' : '/trading/perps-live')}
      />

      {/* Spacer */}
      <div style={{ flex: 1 }} />

      {/* Both-running status message */}
      {bothRunning && (
        <span style={{
          fontSize: 8, fontWeight: 700, color: '#00d48a', letterSpacing: '0.14em', ...MONO,
          marginRight: 12, opacity: 0.7,
        }}>
          ⬤ LEARNING
        </span>
      )}
      {!bothRunning && (spotState === 'offline' || perpState === 'offline') && (
        <span style={{
          fontSize: 8, fontWeight: 700, color: '#f84951', letterSpacing: '0.14em', ...MONO,
          marginRight: 12, opacity: 0.8,
          animation: 'blink 2s ease-in-out infinite',
        }}>
          ⚠ BOT OFFLINE
        </span>
      )}

      {/* Notification bell */}
      <div style={{ marginRight: 6 }}>
        <NotificationBell />
      </div>

      {/* UTC Clock */}
      <span style={{
        fontSize: 9, fontWeight: 600, color: 'rgba(255,255,255,0.2)',
        letterSpacing: '0.08em', ...MONO,
      }}>
        {utc}
      </span>
    </div>
  )
}

// ── Shell ─────────────────────────────────────────────────────────────────────

export function Shell() {
  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden' }}>
      <Sidebar />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0 }}>
        <TopBar />
        <RiskBanner />
        <main style={{
          flex: 1,
          overflow: 'auto',
          padding: '20px 24px',
          background: 'transparent',
        }}>
          <Outlet />
        </main>
      </div>
      {/* AI chat — floats over all pages, bottom-right */}
      <AiChat />
      {/* Signal toasts — top-right ALERT notifications via WebSocket */}
      <SignalToast />
      {/* Trade toasts — bottom-right open/close notifications via WebSocket */}
      <TradeToast />
    </div>
  )
}
