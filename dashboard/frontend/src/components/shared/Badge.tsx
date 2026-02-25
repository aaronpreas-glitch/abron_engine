interface BadgeProps { label: string; color?: 'green' | 'amber' | 'red' | 'blue' | 'muted' | 'purple' }

const palette = {
  green:  'background:#1a3a22;color:#39d353;border:1px solid #2d6a35',
  amber:  'background:#3a2c00;color:#f0a500;border:1px solid #6a4e00',
  red:    'background:#3a1a1a;color:#f85149;border:1px solid #6a2a2a',
  blue:   'background:#1a2a3a;color:#58a6ff;border:1px solid #2a4a6a',
  purple: 'background:#2a1a3a;color:#bc8cff;border:1px solid #4a2a6a',
  muted:  'background:#1c2128;color:#8b949e;border:1px solid #30363d',
}

export function Badge({ label, color = 'muted' }: BadgeProps) {
  return (
    <span className="badge" style={Object.fromEntries(palette[color].split(';').map(s => { const [k,v]=s.split(':'); return [k.replace(/-([a-z])/g,(_,c)=>c.toUpperCase()),v] }))}>
      {label}
    </span>
  )
}
