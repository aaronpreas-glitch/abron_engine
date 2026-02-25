import { useState } from 'react'
import { NavLink } from 'react-router-dom'

// ─── Nav config ──────────────────────────────────────────────────────────────

const NAV_GROUPS = [
  {
    label: '',
    links: [
      { to: '/', label: 'Overview', icon: '⌂' },
    ],
  },
  {
    label: 'MARKET',
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
  {
    label: 'TRADING',
    links: [
      { to: '/executor',  label: 'Executor',  icon: '⚙' },
      { to: '/positions', label: 'Journal',   icon: '▤' },
      { to: '/sol',       label: 'SOL/Perps', icon: '◇' },
      { to: '/risk',      label: 'Risk',      icon: '⊘' },
    ],
  },
  {
    label: 'SYSTEM',
    links: [
      { to: '/config', label: 'Config', icon: '≡' },
    ],
  },
]

// ─── Component ────────────────────────────────────────────────────────────────

export function Sidebar() {
  const [expanded, setExpanded] = useState(false)

  const W_COLLAPSED = 52
  const W_EXPANDED  = 192

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
        {NAV_GROUPS.map((group, gi) => (
          <div key={group.label || gi} style={{ marginBottom: 2 }}>

            {/* Group label — only when expanded */}
            {group.label && expanded && (
              <div style={{
                padding: '9px 14px 2px',
                fontSize: 8.5, fontWeight: 700,
                letterSpacing: '0.2em',
                color: 'rgba(255,255,255,0.16)',
                fontFamily: 'JetBrains Mono, monospace',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
              }}>
                {group.label}
              </div>
            )}

            {/* Separator when collapsed */}
            {group.label && !expanded && gi > 0 && (
              <div style={{
                height: 1,
                margin: '5px 10px',
                background: 'rgba(255,255,255,0.06)',
              }} />
            )}

            {group.links.map(({ to, label, icon }) => (
              <NavLink
                key={to}
                to={to}
                end={to === '/'}
                title={!expanded ? label : undefined}
                style={({ isActive }) => ({
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
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
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
                {/* Icon */}
                <span style={{
                  fontSize: expanded ? 13 : 15,
                  lineHeight: 1,
                  flexShrink: 0,
                  width: expanded ? 'auto' : '100%',
                  textAlign: 'center',
                  fontFamily: 'JetBrains Mono, monospace',
                }}>
                  {icon}
                </span>

                {/* Label — only when expanded */}
                {expanded && (
                  <span style={{ fontSize: 11.5, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {label}
                  </span>
                )}
              </NavLink>
            ))}
          </div>
        ))}
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
