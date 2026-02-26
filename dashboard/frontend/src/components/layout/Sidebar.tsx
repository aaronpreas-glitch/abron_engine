import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'

// ── Live status types ─────────────────────────────────────────────────────────

interface ExecStatus { open_positions: number; enabled: boolean; dry_run: boolean }
interface PerpStatus { open_positions: number; enabled: boolean; dry_run: boolean }

// ── Nav config ────────────────────────────────────────────────────────────────

const NAV_GROUPS = [
  {
    label: '',
    links: [
      { to: '/', label: 'Terminal', icon: '⌂', end: true },
    ],
  },
  {
    label: 'INTELLIGENCE',
    links: [
      { to: '/signals',   label: 'Signals',   icon: '◈' },
      { to: '/launches',  label: 'Launches',  icon: '⚡' },
      { to: '/arb',       label: 'Arb',       icon: '⇄' },
      { to: '/watchlist', label: 'Watchlist', icon: '◎' },
      { to: '/news',      label: 'News',      icon: '◉' },
    ],
  },
  {
    label: 'ANALYSIS',
    links: [
      { to: '/performance',    label: 'Performance', icon: '▲' },
      { to: '/outcome-feed',   label: 'Outcomes',    icon: '◆' },
      { to: '/regime',         label: 'Regime',      icon: '≋' },
      { to: '/regime-heatmap', label: 'Heatmap',     icon: '▦' },
      { to: '/brain',          label: 'Brain',       icon: '⬡' },
    ],
  },
]

// Trading links with dot type for live-status coloring
type DotType = 'spot-paper' | 'spot-live' | 'perps-paper' | 'perps-live' | 'none'

const TRADING_LINKS: { to: string; label: string; icon: string; dot: DotType }[] = [
  { to: '/trading/spot-paper',  label: 'Spot Paper',   icon: '◈', dot: 'spot-paper' },
  { to: '/trading/spot-live',   label: 'Spot Live',    icon: '◈', dot: 'spot-live' },
  { to: '/trading/perps-paper', label: 'Perps Paper',  icon: '◇', dot: 'perps-paper' },
  { to: '/trading/perps-live',  label: 'Perps Live',   icon: '◇', dot: 'perps-live' },
  { to: '/positions',           label: 'Journal',      icon: '▤', dot: 'none' },
  { to: '/risk',                label: 'Risk',         icon: '⊘', dot: 'none' },
]

const SYSTEM_LINKS = [
  { to: '/config', label: 'Config', icon: '≡' },
]

// ── Dot colors ────────────────────────────────────────────────────────────────

function dotColor(
  dot: DotType,
  exec: ExecStatus | undefined,
  perps: PerpStatus | undefined,
): string {
  if (dot === 'spot-paper') {
    if (!exec) return 'rgba(255,255,255,0.15)'
    if (exec.open_positions > 0 && exec.enabled && exec.dry_run) return '#00d48a'
    if (exec.enabled && exec.dry_run) return '#f0a500'
    return 'rgba(255,255,255,0.15)'
  }
  if (dot === 'spot-live') {
    if (!exec) return 'rgba(255,255,255,0.15)'
    if (exec.open_positions > 0 && exec.enabled && !exec.dry_run) return '#f84951'
    if (exec.enabled && !exec.dry_run) return '#f0a500'
    return 'rgba(255,255,255,0.15)'
  }
  if (dot === 'perps-paper') {
    if (!perps) return 'rgba(255,255,255,0.15)'
    if (perps.open_positions > 0 && perps.enabled && perps.dry_run) return '#00d48a'
    if (perps.enabled && perps.dry_run) return '#f0a500'
    return 'rgba(255,255,255,0.15)'
  }
  if (dot === 'perps-live') {
    if (!perps) return 'rgba(255,255,255,0.15)'
    if (perps.open_positions > 0 && perps.enabled && !perps.dry_run) return '#f84951'
    if (perps.enabled && !perps.dry_run) return '#f0a500'
    return 'rgba(255,255,255,0.15)'
  }
  return 'transparent'
}

function dotGlow(color: string): string {
  if (color === '#00d48a') return '0 0 6px #00d48a'
  if (color === '#f84951') return '0 0 6px #f84951'
  if (color === '#f0a500') return '0 0 5px #f0a500'
  return 'none'
}

// ── Component ─────────────────────────────────────────────────────────────────

export function Sidebar() {
  const [expanded, setExpanded] = useState(false)

  const W_COLLAPSED = 52
  const W_EXPANDED  = 200

  // Poll executor + perps status for live dots (low priority, background)
  const { data: execStatus } = useQuery<ExecStatus>({
    queryKey: ['sidebar-exec-status'],
    queryFn: () => api.get('/executor/status').then(r => r.data),
    refetchInterval: 30_000,
    staleTime: 25_000,
  })

  const { data: perpsStatus } = useQuery<PerpStatus>({
    queryKey: ['sidebar-perps-status'],
    queryFn: () => api.get('/perps/status').then(r => r.data),
    refetchInterval: 30_000,
    staleTime: 25_000,
  })

  const linkStyle = (isActive: boolean, expanded: boolean): React.CSSProperties => ({
    display: 'flex',
    alignItems: 'center',
    gap: expanded ? 10 : 0,
    padding: expanded ? '7px 14px 7px 12px' : '8px 0',
    margin: '1px 6px',
    borderRadius: 8,
    justifyContent: expanded ? 'flex-start' : 'center',
    color: isActive ? '#00d48a' : 'rgba(255,255,255,0.40)',
    background: isActive ? 'rgba(0,212,138,0.1)' : 'transparent',
    border: isActive ? '1px solid rgba(0,212,138,0.2)' : '1px solid transparent',
    backdropFilter: isActive ? 'blur(8px)' : 'none',
    textDecoration: 'none',
    fontSize: 12,
    fontWeight: isActive ? 600 : 400,
    transition: 'all 0.15s',
    whiteSpace: 'nowrap' as const,
    overflow: 'hidden',
    position: 'relative' as const,
  })

  return (
    <nav
      style={{
        width: expanded ? W_EXPANDED : W_COLLAPSED,
        minWidth: expanded ? W_EXPANDED : W_COLLAPSED,
        background: 'rgba(4,6,10,0.88)',
        backdropFilter: 'blur(28px) saturate(180%)',
        WebkitBackdropFilter: 'blur(28px) saturate(180%)',
        borderRight: '1px solid rgba(255,255,255,0.07)',
        display: 'flex',
        flexDirection: 'column',
        flexShrink: 0,
        overflow: 'hidden',
        position: 'relative',
        transition: 'width 0.22s cubic-bezier(0.4,0,0.2,1), min-width 0.22s cubic-bezier(0.4,0,0.2,1)',
        zIndex: 100,
      }}
      onMouseEnter={() => setExpanded(true)}
      onMouseLeave={() => setExpanded(false)}
    >
      {/* Inner left highlight */}
      <div style={{
        position: 'absolute', top: 0, left: 0, bottom: 0, width: 1,
        background: 'linear-gradient(180deg, transparent 0%, rgba(0,212,138,0.1) 30%, rgba(0,212,138,0.1) 70%, transparent 100%)',
        pointerEvents: 'none', zIndex: 1,
      }} />

      {/* Logo */}
      <div style={{
        padding: '13px 0',
        borderBottom: '1px solid rgba(255,255,255,0.06)',
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        paddingLeft: expanded ? 12 : 0,
        justifyContent: expanded ? 'flex-start' : 'center',
        transition: 'padding 0.22s',
        flexShrink: 0,
        minHeight: 54,
      }}>
        <div style={{
          width: 28, height: 28, borderRadius: 8,
          background: 'linear-gradient(135deg, #00d48a 0%, #006b48 100%)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 13, fontWeight: 900, color: '#000',
          fontFamily: 'JetBrains Mono, monospace',
          flexShrink: 0,
          boxShadow: '0 0 18px rgba(0,212,138,0.4), 0 2px 6px rgba(0,0,0,0.5)',
        }}>A</div>
        {expanded && (
          <div style={{ overflow: 'hidden', whiteSpace: 'nowrap' }}>
            <div style={{
              color: 'rgba(255,255,255,0.92)', fontWeight: 700, fontSize: 11,
              letterSpacing: '0.08em', fontFamily: 'JetBrains Mono, monospace',
            }}>ABRONS ENGINE</div>
            <div style={{ color: 'rgba(255,255,255,0.22)', fontSize: 8.5, letterSpacing: '0.14em', marginTop: 1 }}>
              TERMINAL
            </div>
          </div>
        )}
      </div>

      {/* Nav */}
      <div style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', padding: '6px 0 8px' }}>

        {/* Standard groups */}
        {NAV_GROUPS.map((group, gi) => (
          <div key={group.label || gi} style={{ marginBottom: 2 }}>
            {group.label && expanded && (
              <div style={{
                padding: '9px 14px 2px',
                fontSize: 8.5, fontWeight: 700,
                letterSpacing: '0.2em',
                color: 'rgba(255,255,255,0.16)',
                fontFamily: 'JetBrains Mono, monospace',
                whiteSpace: 'nowrap', overflow: 'hidden',
              }}>
                {group.label}
              </div>
            )}
            {group.label && !expanded && gi > 0 && (
              <div style={{ height: 1, margin: '5px 10px', background: 'rgba(255,255,255,0.06)' }} />
            )}

            {group.links.map(({ to, label, icon, end }: { to: string; label: string; icon: string; end?: boolean }) => (
              <NavLink
                key={to}
                to={to}
                end={end ?? false}
                title={!expanded ? label : undefined}
                style={({ isActive }) => linkStyle(isActive, expanded)}
                onMouseEnter={e => {
                  const el = e.currentTarget
                  if (!el.style.background.includes('0.1)')) {
                    el.style.background = 'rgba(255,255,255,0.05)'
                    el.style.color = 'rgba(255,255,255,0.72)'
                  }
                }}
                onMouseLeave={e => {
                  const el = e.currentTarget
                  if (!el.style.background.includes('0.1)')) {
                    el.style.background = 'transparent'
                    el.style.color = 'rgba(255,255,255,0.40)'
                  }
                }}
              >
                <span style={{
                  fontSize: expanded ? 13 : 15, lineHeight: 1, flexShrink: 0,
                  width: expanded ? 'auto' : '100%', textAlign: 'center',
                  fontFamily: 'JetBrains Mono, monospace',
                }}>
                  {icon}
                </span>
                {expanded && (
                  <span style={{ fontSize: 11.5, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {label}
                  </span>
                )}
              </NavLink>
            ))}
          </div>
        ))}

        {/* Trading section */}
        <div style={{ marginBottom: 2 }}>
          {expanded ? (
            <div style={{
              padding: '9px 14px 2px',
              fontSize: 8.5, fontWeight: 700,
              letterSpacing: '0.2em',
              color: 'rgba(255,255,255,0.16)',
              fontFamily: 'JetBrains Mono, monospace',
              whiteSpace: 'nowrap', overflow: 'hidden',
            }}>
              TRADING
            </div>
          ) : (
            <div style={{ height: 1, margin: '5px 10px', background: 'rgba(255,255,255,0.06)' }} />
          )}

          {TRADING_LINKS.map(({ to, label, icon, dot }) => {
            const dc = dot !== 'none' ? dotColor(dot, execStatus, perpsStatus) : 'transparent'
            const glow = dotGlow(dc)
            const isActiveDot = dc !== 'transparent' && dc !== 'rgba(255,255,255,0.15)'

            return (
              <NavLink
                key={to}
                to={to}
                title={!expanded ? label : undefined}
                style={({ isActive }) => ({
                  ...linkStyle(isActive, expanded),
                  paddingRight: expanded ? (dot !== 'none' ? 28 : 14) : 0,
                })}
                onMouseEnter={e => {
                  const el = e.currentTarget
                  if (!el.style.background.includes('0.1)')) {
                    el.style.background = 'rgba(255,255,255,0.05)'
                    el.style.color = 'rgba(255,255,255,0.72)'
                  }
                }}
                onMouseLeave={e => {
                  const el = e.currentTarget
                  if (!el.style.background.includes('0.1)')) {
                    el.style.background = 'transparent'
                    el.style.color = 'rgba(255,255,255,0.40)'
                  }
                }}
              >
                <span style={{
                  fontSize: expanded ? 13 : 15, lineHeight: 1, flexShrink: 0,
                  width: expanded ? 'auto' : '100%', textAlign: 'center',
                  fontFamily: 'JetBrains Mono, monospace',
                }}>
                  {icon}
                </span>
                {expanded && (
                  <>
                    <span style={{ fontSize: 11.5, overflow: 'hidden', textOverflow: 'ellipsis', flex: 1 }}>
                      {label}
                    </span>
                    {dot !== 'none' && (
                      <span style={{
                        position: 'absolute', right: 10,
                        width: 5, height: 5, borderRadius: '50%',
                        background: dc,
                        boxShadow: isActiveDot ? glow : 'none',
                        flexShrink: 0,
                      }} />
                    )}
                  </>
                )}
                {/* Collapsed: dot indicator on top-right of icon */}
                {!expanded && dot !== 'none' && dc !== 'transparent' && dc !== 'rgba(255,255,255,0.15)' && (
                  <span style={{
                    position: 'absolute', top: 5, right: 5,
                    width: 4, height: 4, borderRadius: '50%',
                    background: dc, boxShadow: glow,
                  }} />
                )}
              </NavLink>
            )
          })}
        </div>

        {/* System section */}
        <div style={{ marginBottom: 2 }}>
          {expanded ? (
            <div style={{
              padding: '9px 14px 2px',
              fontSize: 8.5, fontWeight: 700,
              letterSpacing: '0.2em',
              color: 'rgba(255,255,255,0.16)',
              fontFamily: 'JetBrains Mono, monospace',
            }}>
              SYSTEM
            </div>
          ) : (
            <div style={{ height: 1, margin: '5px 10px', background: 'rgba(255,255,255,0.06)' }} />
          )}
          {SYSTEM_LINKS.map(({ to, label, icon }) => (
            <NavLink
              key={to}
              to={to}
              title={!expanded ? label : undefined}
              style={({ isActive }) => linkStyle(isActive, expanded)}
              onMouseEnter={e => {
                const el = e.currentTarget
                if (!el.style.background.includes('0.1)')) {
                  el.style.background = 'rgba(255,255,255,0.05)'
                  el.style.color = 'rgba(255,255,255,0.72)'
                }
              }}
              onMouseLeave={e => {
                const el = e.currentTarget
                if (!el.style.background.includes('0.1)')) {
                  el.style.background = 'transparent'
                  el.style.color = 'rgba(255,255,255,0.40)'
                }
              }}
            >
              <span style={{
                fontSize: expanded ? 13 : 15, lineHeight: 1, flexShrink: 0,
                width: expanded ? 'auto' : '100%', textAlign: 'center',
                fontFamily: 'JetBrains Mono, monospace',
              }}>
                {icon}
              </span>
              {expanded && (
                <span style={{ fontSize: 11.5, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {label}
                </span>
              )}
            </NavLink>
          ))}
        </div>
      </div>

      {/* Footer */}
      <div style={{
        borderTop: '1px solid rgba(255,255,255,0.06)',
        padding: expanded ? '9px 14px' : '9px 0',
        textAlign: expanded ? 'left' : 'center',
        fontSize: 8.5,
        color: 'rgba(255,255,255,0.13)',
        fontFamily: 'JetBrains Mono, monospace',
        letterSpacing: '0.08em',
        whiteSpace: 'nowrap',
        overflow: 'hidden',
        flexShrink: 0,
      }}>
        {expanded ? 'solana · personal' : '◈'}
      </div>
    </nav>
  )
}
