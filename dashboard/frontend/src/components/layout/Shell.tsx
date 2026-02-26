import { useEffect, useState } from 'react'
import { Outlet } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { RiskBanner } from './RiskBanner'
import { Sidebar } from './Sidebar'
import { AiChat } from '../ai/AiChat'
import { SignalToast } from '../signals/SignalToast'
import { api } from '../../api'

// ── Types ─────────────────────────────────────────────────────────────────────

interface SnapshotData {
  regime: { regime_label: string; regime_score: number } | null
  open_positions: { symbol: string }[]
}

interface ExecStatus { open_positions: number; enabled: boolean; dry_run: boolean }
interface PerpStatus { open_positions: number; enabled: boolean; dry_run: boolean }

// ── Top Status Bar ─────────────────────────────────────────────────────────────

const MONO: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }

function regimeColor(label: string) {
  if (!label) return 'rgba(255,255,255,0.35)'
  if (label.includes('RISK_ON') || label.includes('BULL')) return '#00d48a'
  if (label.includes('RISK_OFF') || label.includes('BEAR')) return '#f04f4f'
  return '#f0a500'
}

function TopBar() {
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
    refetchInterval: 30_000,
    staleTime: 25_000,
  })

  const { data: perps } = useQuery<PerpStatus>({
    queryKey: ['shell-perps-status'],
    queryFn: () => api.get('/perps/status').then(r => r.data),
    refetchInterval: 30_000,
    staleTime: 25_000,
  })

  const regime = snap?.regime
  const rc = regime ? regimeColor(regime.regime_label) : 'rgba(255,255,255,0.35)'
  const spotOpen = exec?.open_positions ?? 0
  const perpsOpen = perps?.open_positions ?? 0

  return (
    <div style={{
      height: 30,
      background: 'rgba(4,6,12,0.9)',
      backdropFilter: 'blur(20px)',
      borderBottom: '1px solid rgba(255,255,255,0.06)',
      display: 'flex',
      alignItems: 'center',
      paddingLeft: 16,
      paddingRight: 16,
      gap: 0,
      flexShrink: 0,
      overflow: 'hidden',
    }}>
      {/* Brand */}
      <span style={{
        fontSize: 9.5, fontWeight: 800, color: 'rgba(255,255,255,0.4)',
        letterSpacing: '0.18em', ...MONO, paddingRight: 14,
        borderRight: '1px solid rgba(255,255,255,0.07)',
      }}>
        ABRONS ENGINE
      </span>

      {/* Regime pill */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '0 14px', borderRight: '1px solid rgba(255,255,255,0.07)' }}>
        <span style={{
          width: 5, height: 5, borderRadius: '50%',
          background: rc, boxShadow: `0 0 6px ${rc}`,
          display: 'inline-block', flexShrink: 0,
        }} />
        <span style={{ fontSize: 9.5, fontWeight: 700, color: rc, letterSpacing: '0.06em', ...MONO }}>
          {regime?.regime_label?.replace(/_/g, ' ') || '—'}
        </span>
        {regime?.regime_score != null && (
          <span style={{ fontSize: 8, color: 'rgba(255,255,255,0.22)', ...MONO }}>
            {regime.regime_score.toFixed(0)}
          </span>
        )}
      </div>

      {/* Spot open */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '0 14px', borderRight: '1px solid rgba(255,255,255,0.07)' }}>
        <span style={{ fontSize: 8.5, color: 'rgba(255,255,255,0.28)', letterSpacing: '0.1em', ...MONO }}>SPOT</span>
        <span style={{
          fontSize: 9.5, fontWeight: 700, ...MONO,
          color: spotOpen > 0 ? '#00d48a' : 'rgba(255,255,255,0.28)',
        }}>
          {spotOpen} open
        </span>
        {exec && (
          <span style={{
            fontSize: 7.5, padding: '1px 5px', borderRadius: 3, fontWeight: 700, ...MONO,
            background: exec.dry_run ? 'rgba(240,165,0,0.1)' : 'rgba(248,81,73,0.1)',
            color: exec.dry_run ? '#f0a500' : '#f84951',
            border: `1px solid ${exec.dry_run ? 'rgba(240,165,0,0.2)' : 'rgba(248,81,73,0.2)'}`,
          }}>
            {exec.dry_run ? 'PAPER' : 'LIVE'}
          </span>
        )}
      </div>

      {/* Perps open */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '0 14px', borderRight: '1px solid rgba(255,255,255,0.07)' }}>
        <span style={{ fontSize: 8.5, color: 'rgba(255,255,255,0.28)', letterSpacing: '0.1em', ...MONO }}>PERPS</span>
        <span style={{
          fontSize: 9.5, fontWeight: 700, ...MONO,
          color: perpsOpen > 0 ? '#00d48a' : 'rgba(255,255,255,0.28)',
        }}>
          {perpsOpen} open
        </span>
        {perps && (
          <span style={{
            fontSize: 7.5, padding: '1px 5px', borderRadius: 3, fontWeight: 700, ...MONO,
            background: perps.dry_run ? 'rgba(240,165,0,0.1)' : 'rgba(248,81,73,0.1)',
            color: perps.dry_run ? '#f0a500' : '#f84951',
            border: `1px solid ${perps.dry_run ? 'rgba(240,165,0,0.2)' : 'rgba(248,81,73,0.2)'}`,
          }}>
            {perps.dry_run ? 'PAPER' : 'LIVE'}
          </span>
        )}
      </div>

      {/* Spacer */}
      <div style={{ flex: 1 }} />

      {/* UTC Clock */}
      <span style={{
        fontSize: 9.5, fontWeight: 600, color: 'rgba(255,255,255,0.22)',
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
    </div>
  )
}
